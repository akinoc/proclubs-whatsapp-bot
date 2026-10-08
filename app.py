
import os, re, hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import create_engine, Column, Integer, String, Date, Time, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Istanbul"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")
ADMIN_PHONE = re.sub(r"\D", "", os.getenv("ADMIN_PHONE", ""))
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_WABA_ID = os.getenv("META_WABA_ID", "")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v23.0")
META_INVITE_TEMPLATE_NAME = os.getenv("META_INVITE_TEMPLATE_NAME", "")
META_INVITE_TEMPLATE_LANG = os.getenv("META_INVITE_TEMPLATE_LANG", "tr")

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

Base.metadata.create_all(bind=engine)
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
         "*KADRO* — Kayıtlı oyuncular\n*DURUM* — Kendi durumun\n*SİL* — Bugünkü cevabı sil\n*YARDIM* — Komutlar")
    if admin:
        t += ("\n\n👑 *ADMIN*\n*BEKLEYEN*\n*ONAY 12*\n*RED 12*\n"
              "*DAVET 905xxxxxxxxx İsim*\n*DAVETLER*\n*ÜYELER*\n*PASIF 12*\n*KADRO DETAY*")
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
        print("META SEND ERROR",r.status_code,r.text)
        r.raise_for_status()

async def send_invite(phone, name):
    if not META_INVITE_TEMPLATE_NAME: raise RuntimeError("invite template not configured")
    payload={"messaging_product":"whatsapp","to":norm(phone),"type":"template",
             "template":{"name":META_INVITE_TEMPLATE_NAME,"language":{"code":META_INVITE_TEMPLATE_LANG},
                         "components":[{"type":"body","parameters":[{"type":"text","text":name}]}]}}
    async with httpx.AsyncClient(timeout=20) as c:
        r=await c.post(msg_url(),headers={"Authorization":f"Bearer {META_ACCESS_TOKEN}","Content-Type":"application/json"},json=payload)
    if r.status_code>=400:
        print("META TEMPLATE ERROR",r.status_code,r.text); r.raise_for_status()

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
def root(): return {"service":"proclubs-meta-whatsapp-bot","status":"ok"}

@app.get("/health")
def health(): return {"ok":True,"time":datetime.now(TZ).isoformat()}

@app.get("/webhook",response_class=PlainTextResponse)
async def verify(hub_mode:str|None=Query(None,alias="hub.mode"),
                 hub_verify_token:str|None=Query(None,alias="hub.verify_token"),
                 hub_challenge:str|None=Query(None,alias="hub.challenge")):
    if hub_mode=="subscribe" and META_VERIFY_TOKEN and hub_verify_token==META_VERIFY_TOKEN and hub_challenge:
        return hub_challenge
    raise HTTPException(403,"Webhook verification failed")

@app.post("/webhook")
async def webhook(request:Request):
    data=await request.json()
    try:
        for entry in data.get("entry",[]):
            for change in entry.get("changes",[]):
                for m in change.get("value",{}).get("messages",[]):
                    if m.get("type")!="text": continue
                    phone=norm(m.get("from",""))
                    text=m.get("text",{}).get("body","")
                    if phone and text:
                        await send_text(phone,await process(phone,text))
    except Exception as e:
        print("WEBHOOK ERROR",repr(e))
    return {"ok":True}

if __name__=="__main__":
    import uvicorn
    uvicorn.run("app:app",host="0.0.0.0",port=int(os.getenv("PORT","8000")))
