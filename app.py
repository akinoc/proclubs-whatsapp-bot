
import os, re, hashlib, hmac, logging, time
from fastapi import BackgroundTasks
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import create_engine, Column, Integer, String, Date, Time, DateTime, Boolean, ForeignKey, UniqueConstraint, or_
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.exc import IntegrityError

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Istanbul"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")
ADMIN_PHONE = re.sub(r"\D", "", os.getenv("ADMIN_PHONE", ""))
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_WABA_ID = os.getenv("META_WABA_ID", "")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v23.0")
META_INVITE_TEMPLATE_NAME = os.getenv("META_INVITE_TEMPLATE_NAME", "")
META_INVITE_TEMPLATE_LANG = os.getenv("META_INVITE_TEMPLATE_LANG", "tr")
REMINDER_CRON_SECRET = os.getenv("REMINDER_CRON_SECRET", "")
META_REMINDER_TEMPLATE_NAME = os.getenv("META_REMINDER_TEMPLATE_NAME", "proclubs_attandance")
META_REMINDER_TEMPLATE_LANG = os.getenv("META_REMINDER_TEMPLATE_LANG", "tr")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True,
                       connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    phone = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(100))
    status = Column(String(32), nullable=False, default="PENDING_NAME")
    is_admin = Column(Boolean, nullable=False, default=False)
    invited_by_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    approved_at = Column(DateTime(timezone=True))

class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    start_time = Column(Time)
    end_time = Column(Time)
    status = Column(String(20), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    user = relationship("User")
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_user_date"),)

class IncomingMessage(Base):
    """Persisted deduplication across Render restarts and multiple workers."""
    __tablename__ = "incoming_messages"
    message_id = Column(String(255), primary_key=True)
    sender = Column(String(32), nullable=False)
    received_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    status = Column(String(16), nullable=False, default="RECEIVED")

class ReminderPreference(Base):
    """User reminder preference: missing row means existing consent; False is opt-out."""
    __tablename__ = "reminder_preferences"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)


class ReminderDispatch(Base):
    """One claim per user/day so concurrent cron calls never send twice."""
    __tablename__ = "reminder_dispatches"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    state = Column(String(20), nullable=False, default="CLAIMED")
    claimed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(TZ))
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_reminder_user_day"),)


Base.metadata.create_all(bind=engine)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("proclubs")
app = FastAPI(title="Pro Clubs Meta WhatsApp Bot")

def norm(v): return re.sub(r"\D", "", v or "")
def today(): return datetime.now(TZ).date()
def fmt(t): return t.strftime("%H:%M") if t else ""
def get_user(db, phone): return db.query(User).filter(User.phone == norm(phone)).first()

def ensure_admin(db):
    if not ADMIN_PHONE: return
    u = get_user(db, ADMIN_PHONE)
    if not u:
        u = User(phone=ADMIN_PHONE, name="Akın", status="ACTIVE", is_admin=True,
                 invited_by_admin=True, approved_at=datetime.now(TZ))
        db.add(u)
    else:
        u.is_admin = True
        u.status = "ACTIVE"
        if not u.name: u.name = "Akın"
    db.commit()

def parse_time(s):
    s = s.strip().replace(" ", "")
    m = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", s)
    if m:
        return datetime.strptime(f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", "%H:%M").time(), None
    m = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)-([01]?\d|2[0-3])[:.]([0-5]\d)", s)
    if m:
        a = datetime.strptime(f"{int(m.group(1)):02d}:{int(m.group(2)):02d}", "%H:%M").time()
        b = datetime.strptime(f"{int(m.group(3)):02d}:{int(m.group(4)):02d}", "%H:%M").time()
        return a, b
    return None

def upsert_attendance(db, user, status, start=None, end=None):
    r = db.query(Attendance).filter(Attendance.user_id == user.id, Attendance.date == today()).first()
    if not r:
        r = Attendance(user_id=user.id, date=today(), status=status)
        db.add(r)
    r.status, r.start_time, r.end_time = status, start, end
    r.updated_at = datetime.now(TZ)
    db.commit()

def squad(db, detail=False):
    users = db.query(User).filter(User.status == "ACTIVE").order_by(User.name.asc()).all()
    lines = ["👥 *PRO CLUBS KADROSU*", ""]
    if not users: lines.append("Henüz aktif oyuncu yok.")
    for i,u in enumerate(users,1):
        lines.append(f"{i}. {u.name}" + (f" — +{u.phone} — ID:{u.id}" if detail else ""))
    lines += ["", f"Toplam kayıtlı oyuncu: *{len(users)}*"]
    return "\n".join(lines)

MUALLAK_FAALIYETLER = [
    "Sarma sarıyor 🍃",
    "Kuaföre gitti 💇",
    "Altın gününe katıldı 🫖",
    "Manikür yaptırıyor 💅",
    "Komşuyla kahve içiyor ☕",
    "Çeyiz bakmaya çıktı 🧵",
    "Gelinlik provasında 👰",
    "Pazarda indirim kovalıyor 🛍️",
    "Kısır yoğuruyor 🥗",
    "Dizisinin yeni bölümünü izliyor 📺",
    "Börek açıyor 🥐",
    "Dedikodu hattında meşgul 📞",
    "Perde yıkıyor 🧺",
    "Arkadaşlarıyla brunch yapıyor 🥞",
    "Saç boyası bekliyor 🎨",
]


def muallak_faaliyet(user, day):
    # Oyuncu ve gün bazında sabittir; ertesi gün değişebilir.
    key = f"{day.isoformat()}:{user.id}".encode("utf-8")
    index = int.from_bytes(hashlib.sha256(key).digest()[:8], "big") % len(MUALLAK_FAALIYETLER)
    return MUALLAK_FAALIYETLER[index]


def attendance_list(db):
    active_users = db.query(User).filter(User.status == "ACTIVE").order_by(User.name.asc()).all()
    rows = db.query(Attendance).join(User).filter(Attendance.date == today(), User.status == "ACTIVE").all()
    coming = sorted([r for r in rows if r.status == "COMING"], key=lambda r: (r.start_time or datetime.min.time(), r.user.name or ""))
    out = sorted([r for r in rows if r.status == "NOT_COMING"], key=lambda r: r.user.name or "")
    answered_ids = {r.user_id for r in rows if r.status in ("COMING", "NOT_COMING")}
    muallaklar = [u for u in active_users if u.id not in answered_ids]

    lines = [f"🎮 *PRO CLUBS — {datetime.now(TZ).strftime('%d.%m.%Y')}*", ""]
    if coming:
        for r in coming:
            when = f"{fmt(r.start_time)}–{fmt(r.end_time)}" if r.end_time else fmt(r.start_time)
            lines.append(f"✅ {r.user.name} — {when}")
    else:
        lines.append("Henüz geleceğini bildiren yok.")

    if out:
        lines += ["", "❌ *Yokum diyenler*"] + [f"• {r.user.name}" for r in out]

    lines += ["", f"❔ *MUALLAKLAR ({len(muallaklar)})*"]
    if muallaklar:
        lines += [f"• {u.name} — {muallak_faaliyet(u, today())}" for u in muallaklar]
    else:
        lines.append("Muallak oyuncu yok. 🎉")

    lines += ["", f"👥 Cevap veren: *{len(answered_ids)}/{len(active_users)}*",
              f"✅ Gelen: *{len(coming)}*", f"❌ Gelmeyen: *{len(out)}*",
              f"❔ Muallaklar: *{len(muallaklar)}*"]
    return "\n".join(lines)

def help_text(admin=False):
    t = ("🎮 *PRO CLUBS KOMUTLARI*\n\n*KAYIT* — Üyelik başlat\n*19:00* — Geliş saatin\n"
         "*19:00-23:00* — Saat aralığın\n*YOKUM* — Bu akşam yokum\n*LISTE* — Bu akşamki liste\n"
         "*KADRO* — Kayıtlı oyuncular\n*DURUM* — Kendi durumun\n*SİL* — Bugünkü cevabı sil\n*HATIRLATMA AÇ* — Hatırlatmaları yeniden aç\n*HATIRLATMA KAPAT* — Hatırlatmaları durdur\n*YARDIM* — Komutlar")
    if admin:
        t += ("\n\n👑 *ADMIN*\n*BEKLEYEN*\n*ONAY 12*\n*RED 12*\n"
              "*DAVET 905xxxxxxxxx İsim*\n*DAVETLER*\n*ÜYELER*\n*PASIF 12*\n*ISIM 12 Yeni İsim*\n*KADRO DETAY*")
    return t

def msg_url():
    return f"https://graph.facebook.com/{META_GRAPH_VERSION}/{META_PHONE_NUMBER_ID}/messages"

async def send_text(phone, text):
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        print("[DEV SEND]", phone, text); return
    payload={"messaging_product":"whatsapp","to":norm(phone),"type":"text","text":{"preview_url":False,"body":text}}
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.post(msg_url(),headers={"Authorization":f"Bearer {META_ACCESS_TOKEN}","Content-Type":"application/json"},json=payload)
    if r.status_code>=400:
        logger.error("META_SEND_ERROR status=%s body=%s", r.status_code, r.text[:500])
        r.raise_for_status()
    try:
        outgoing_ids=[item.get("id") for item in r.json().get("messages", [])]
    except (ValueError, AttributeError):
        outgoing_ids=[]
    logger.info("META_SEND_OK to_suffix=%s outgoing_ids=%s", norm(phone)[-4:], outgoing_ids)

async def send_invite(phone, name):
    if not META_INVITE_TEMPLATE_NAME: raise RuntimeError("invite template not configured")
    payload={"messaging_product":"whatsapp","to":norm(phone),"type":"template",
             "template":{"name":META_INVITE_TEMPLATE_NAME,"language":{"code":META_INVITE_TEMPLATE_LANG},
                         "components":[{"type":"body","parameters":[{"type":"text","text":name}]}]}}
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.post(msg_url(),headers={"Authorization":f"Bearer {META_ACCESS_TOKEN}","Content-Type":"application/json"},json=payload)
    if r.status_code>=400:
        print("META TEMPLATE ERROR",r.status_code,r.text); r.raise_for_status()

async def send_reminder_template(phone: str):
    """Business-initiated template message, valid outside WhatsApp's 24h window.

    The Meta-approved template must have NO body variables/components.
    Suggested body (create in WhatsApp Manager):
    "🎮 Proclubs hatırlatması: Bugünkü katılımını henüz bildirmedin.
    Geleceksen saatini (ör. 20:00), gelmeyeceksen YOKUM yaz.
    Hatırlatmaları durdurmak için HATIRLATMA KAPAT yaz."
    """
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        raise RuntimeError("Missing Meta WhatsApp credentials")
    payload = {
        "messaging_product": "whatsapp", "to": norm(phone), "type": "template",
        "template": {
            "name": META_REMINDER_TEMPLATE_NAME,
            "language": {"code": META_REMINDER_TEMPLATE_LANG},
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            msg_url(),
            headers={"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.is_error:
        logger.error("REMINDER_META_ERROR status=%s detail=%s", response.status_code, response.text[:800])
        response.raise_for_status()
    return response.json()


def _claim_reminder(db, user_id, day):
    claim = ReminderDispatch(user_id=user_id, date=day, state="CLAIMED")
    db.add(claim)
    try:
        db.commit()
        return claim.id
    except IntegrityError:
        db.rollback()
        return None


async def dispatch_reminders():
    """Run once per calendar day via authenticated external scheduler."""
    day = today()
    sent, skipped, failed = 0, 0, 0
    with SessionLocal() as db:
        eligible = (
            db.query(User)
            .outerjoin(ReminderPreference, ReminderPreference.user_id == User.id)
            .outerjoin(Attendance, (Attendance.user_id == User.id) & (Attendance.date == day))
            .filter(User.status == "ACTIVE", or_(ReminderPreference.user_id.is_(None), ReminderPreference.enabled.is_(True)), Attendance.id.is_(None))
            .order_by(User.id.asc())
            .all()
        )
        for user in eligible:
            # Recheck status because the player may respond while this job runs.
            db.expire_all()
            still_unanswered = not db.query(Attendance.id).filter(
                Attendance.user_id == user.id, Attendance.date == day
            ).first()
            still_active = db.query(User.id).filter(User.id == user.id, User.status == "ACTIVE").first()
            opted_out = db.query(ReminderPreference.user_id).filter(
                ReminderPreference.user_id == user.id,
                ReminderPreference.enabled.is_(False),
            ).first()
            if not (still_unanswered and still_active and not opted_out):
                skipped += 1
                continue
            claim_id = _claim_reminder(db, user.id, day)
            if claim_id is None:
                skipped += 1
                continue
            try:
                await send_reminder_template(user.phone)
                claim = db.get(ReminderDispatch, claim_id)
                claim.state = "SENT"
                db.commit()
                sent += 1
            except Exception:
                # Keep claimed record to avoid blind double sends after a
                # timeout (Meta may have accepted the request already).
                claim = db.get(ReminderDispatch, claim_id)
                claim.state = "ERROR_REVIEW"
                db.commit()
                failed += 1
                logger.exception("REMINDER_SEND_FAILED user_id=%s", user.id)
    logger.info("REMINDER_FINISHED day=%s sent=%s skipped=%s failed=%s", day, sent, skipped, failed)
    return {"date": day.isoformat(), "sent": sent, "skipped": skipped, "failed": failed}


async def process(phone, text):
    phone, raw = norm(phone), (text or "").strip()
    cmd = raw.upper()
    db=SessionLocal()
    try:
        ensure_admin(db)
        user=get_user(db,phone)

        if user and user.is_admin:
            if cmd=="BEKLEYEN":
                rows=db.query(User).filter(User.status=="PENDING_APPROVAL").order_by(User.id).all()
                return "✅ Onay bekleyen kullanıcı yok." if not rows else "🕐 *ONAY BEKLEYENLER*\n\n"+"\n".join(f"ID:{u.id} — {u.name} — +{u.phone}" for u in rows)
            m=re.fullmatch(r"ONAY\s+(\d+)",cmd)
            if m:
                t=db.get(User,int(m.group(1)))
                if not t:return "❌ Kullanıcı bulunamadı."
                t.status="ACTIVE"; t.approved_at=datetime.now(TZ); db.commit()
                try: await send_text(t.phone,"✅ Kaydın onaylandı. Saatini yazabilirsin: *19:00* veya *19:00-23:00*")
                except: pass
                return f"✅ {t.name} onaylandı."
            m=re.fullmatch(r"RED\s+(\d+)",cmd)
            if m:
                t=db.get(User,int(m.group(1)))
                if not t:return "❌ Kullanıcı bulunamadı."
                t.status="REJECTED"; db.commit()
                return f"✅ {t.name} reddedildi."
            m=re.match(r"^DAVET\s+(\+?\d{10,15})\s+(.+)$",raw,re.I)
            if m:
                p,n=norm(m.group(1)),m.group(2).strip()
                t=get_user(db,p)
                if not t:
                    t=User(phone=p,name=n,status="INVITED",invited_by_admin=True); db.add(t)
                else:
                    t.name=n; t.status="INVITED"; t.invited_by_admin=True
                db.commit()
                if not META_INVITE_TEMPLATE_NAME:
                    return f"✅ {n} davetli kaydedildi.\n⚠️ Meta'da onaylı davet template'i oluşturup META_INVITE_TEMPLATE_NAME tanımlayınca WhatsApp daveti otomatik gider."
                try:
                    await send_invite(p,n); return f"✅ {n} davet edildi."
                except Exception as e:
                    return f"✅ {n} davetli kaydedildi. ⚠️ Template gönderilemedi: {type(e).__name__}"
            if cmd=="DAVETLER":
                rows=db.query(User).filter(User.status=="INVITED").order_by(User.id).all()
                return "✅ Bekleyen davet yok." if not rows else "✉️ *BEKLEYEN DAVETLER*\n\n"+"\n".join(f"ID:{u.id} — {u.name} — +{u.phone}" for u in rows)
            if cmd=="ÜYELER":
                rows=db.query(User).order_by(User.id).all()
                return "👥 *TÜM ÜYELER*\n\n"+"\n".join(f"ID:{u.id} — {u.name or '-'} — {u.status} — +{u.phone}" for u in rows)
            m=re.fullmatch(r"PASIF\s+(\d+)",cmd)
            if m:
                t=db.get(User,int(m.group(1)))
                if not t:return "❌ Kullanıcı bulunamadı."
                if t.is_admin:return "⛔ Admin pasife alınamaz."
                t.status="INACTIVE"; db.commit(); return f"✅ {t.name} pasife alındı."
            m=re.fullmatch(r"(?:ISIM|İSİM)\s+(\d+)\s+(.+)", raw, re.IGNORECASE)
            if m:
                target=db.get(User, int(m.group(1)))
                if not target:
                    return "❌ Bu ID ile kullanıcı bulunamadı. ID için *KADRO DETAY* yaz."
                new_name=" ".join(m.group(2).split())
                if len(new_name) < 2 or len(new_name) > 100:
                    return "❌ İsim 2–100 karakter arasında olmalı."
                old_name=target.name or "(isimsiz)"
                target.name=new_name
                db.commit()
                logger.info("ADMIN_RENAME actor=%s target_id=%s", user.id, target.id)
                return f"✅ Oyuncu adı güncellendi: *{old_name}* → *{new_name}* (ID: {target.id})"
            if cmd=="KADRO DETAY": return squad(db,True)

        if not user:
            if cmd=="KAYIT":
                db.add(User(phone=phone,status="PENDING_NAME")); db.commit(); return "📝 Kayıt için adını yaz."
            return "⛔ Henüz kayıtlı değilsin. Kayıt olmak için *KAYIT* yaz."

        if user.status=="INVITED":
            if cmd=="KAYIT":
                user.status="ACTIVE"; user.approved_at=datetime.now(TZ); db.commit()
                return f"✅ Hoş geldin {user.name}! Davetin doğrulandı. Saatini yazabilirsin: *19:00*"
            return "✉️ Admin tarafından davet edilmişsin. Aktifleştirmek için *KAYIT* yaz."

        if user.status=="PENDING_NAME":
            if cmd=="KAYIT": return "📝 Şimdi adını yaz."
            if len(raw)<2 or len(raw)>100:return "❌ Geçerli bir isim yaz."
            user.name=raw; user.status="PENDING_APPROVAL"; db.commit()
            if ADMIN_PHONE:
                try: await send_text(ADMIN_PHONE,f"🆕 *YENİ ÜYELİK TALEBİ*\n\n{user.name}\n+{user.phone}\nID: {user.id}\n\nOnay: *ONAY {user.id}*\nRed: *RED {user.id}*")
                except: pass
            return "✅ Kayıt talebin alındı. Admin onayı bekleniyor."

        if user.status=="PENDING_APPROVAL": return "🕐 Üyelik talebin admin onayı bekliyor."
        if user.status!="ACTIVE": return "⛔ Üyeliğin aktif değil."

        if cmd in ("HATIRLATMA AÇ", "HATIRLATMA AC", "HATIRLATMA KAPAT"):
            enabled = cmd != "HATIRLATMA KAPAT"
            preference = db.get(ReminderPreference, user.id)
            if preference is None:
                preference = ReminderPreference(user_id=user.id, enabled=enabled)
                db.add(preference)
            else:
                preference.enabled = enabled
            db.commit()
            return ("🔔 Her gün saat 20:00'de, yalnızca cevap vermediğin günlerde "
                    "WhatsApp hatırlatmaları yeniden açıldı. İptal: *HATIRLATMA KAPAT*"
                    if enabled else "🔕 Otomatik hatırlatmalar kapatıldı.")

        if cmd=="YARDIM": return help_text(user.is_admin)
        if cmd=="KADRO": return squad(db)
        if cmd=="LISTE": return attendance_list(db)
        if cmd=="YOKUM":
            upsert_attendance(db,user,"NOT_COMING")
            return f"❌ {user.name} — bu akşam yok olarak işaretlendin."
        if cmd=="DURUM":
            r=db.query(Attendance).filter(Attendance.user_id==user.id,Attendance.date==today()).first()
            if not r:return "ℹ️ Bugün için henüz cevap vermedin."
            if r.status=="NOT_COMING":return f"❌ {user.name} — bu akşam yoksun."
            return f"✅ {user.name} — "+(f"{fmt(r.start_time)}–{fmt(r.end_time)}" if r.end_time else fmt(r.start_time))
        if cmd=="SİL":
            r=db.query(Attendance).filter(Attendance.user_id==user.id,Attendance.date==today()).first()
            if not r:return "ℹ️ Silinecek kayıt yok."
            db.delete(r);db.commit();return "🗑️ Bugünkü cevabın silindi."
        parsed=parse_time(raw)
        if parsed:
            a,b=parsed;upsert_attendance(db,user,"COMING",a,b)
            confirmation = f"✅ {user.name} — bugün *{fmt(a)}"+(f"–{fmt(b)}" if b else "")+"* olarak listeye eklendin."
            return confirmation + "\n\n" + attendance_list(db)
        return help_text(user.is_admin)
    finally:
        db.close()

@app.get("/")
def root():
    return {"service": "proclubs-meta-whatsapp-bot", "status": "ok", "version": "2026-10-20h-template"}


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(TZ).isoformat()}


@app.post("/jobs/daily-reminder")
async def daily_reminder(request: Request):
    # Render Cron Job calls this endpoint. Never expose an unauthenticated blast.
    if not REMINDER_CRON_SECRET:
        raise HTTPException(status_code=503, detail="REMINDER_CRON_SECRET is not configured")
    supplied = request.headers.get("x-cron-secret", "")
    if not hmac.compare_digest(supplied, REMINDER_CRON_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    now = datetime.now(TZ)
    # Do not send on accidental early or late scheduler invocations.
    if now.hour != 20:
        raise HTTPException(status_code=409, detail="Reminder is permitted only 20:00-20:59 Europe/Istanbul")
    # Trigger runs synchronously. At low subscriber counts, response will finish quickly.
    return await dispatch_reminders()


@app.get("/webhook", response_class=PlainTextResponse)
async def verify(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    if (hub_mode == "subscribe" and META_VERIFY_TOKEN
            and hmac.compare_digest(hub_verify_token or "", META_VERIFY_TOKEN)
            and hub_challenge):
        return hub_challenge
    raise HTTPException(status_code=403, detail="Webhook verification failed")


def _claim_message(message_id: str, phone: str) -> bool:
    """Atomic persistent claim: Meta's repeated deliveries receive 200 without a second reply."""
    with SessionLocal() as db:
        db.add(IncomingMessage(message_id=message_id, sender=phone))
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def _update_message_status(message_id: str, status: str):
    with SessionLocal() as db:
        record = db.get(IncomingMessage, message_id)
        if record:
            record.status = status
            db.commit()


async def handle_incoming(message_id: str, phone: str, body: str):
    started = time.monotonic()
    try:
        answer = await process(phone, body)
        if answer:
            await send_text(phone, answer)
        _update_message_status(message_id, "DONE")
        logger.info("MESSAGE_OK id=%s duration_ms=%d", message_id, (time.monotonic()-started)*1000)
    except Exception:
        _update_message_status(message_id, "FAILED")
        logger.exception("MESSAGE_FAILED id=%s", message_id)
        # For failed sends, inspect logs; do not blindly reply twice on retry.


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    # When configured, reject forged webhook payloads using Meta's app secret.
    if META_APP_SECRET:
        signature = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(META_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Invalid signature")
    try:
        import json
        data = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    accepted = 0
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                for msg in change.get("value", {}).get("messages", []):
                    if msg.get("type") != "text":
                        continue
                    message_id = msg.get("id", "")
                    phone = norm(msg.get("from", ""))
                    body = msg.get("text", {}).get("body", "").strip()
                    if not message_id or not phone or not body:
                        continue
                    if _claim_message(message_id, phone):
                        background_tasks.add_task(handle_incoming, message_id, phone, body)
                        accepted += 1
                    else:
                        logger.info("WEBHOOK_DUPLICATE_IGNORED id=%s", message_id)
    except Exception:
        logger.exception("WEBHOOK_ACCEPT_FAILED")
        # Signal a retry instead of silently losing an event on database failure.
        raise HTTPException(status_code=503, detail="Webhook processing unavailable")
    logger.info("WEBHOOK_RECEIVED accepted=%d", accepted)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
