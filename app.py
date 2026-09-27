
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from sqlalchemy import (
    create_engine, Column, Integer, String, Date, Time, DateTime,
    Boolean, ForeignKey, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

APP_TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Istanbul"))
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")
ADMIN_PHONE = re.sub(r"\D", "", os.getenv("ADMIN_PHONE", ""))
DESK360_API_KEY = os.getenv("DESK360_API_KEY", "")
DESK360_INTEGRATION_ID = os.getenv("DESK360_INTEGRATION_ID", "")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
DESK360_BASE_URL = os.getenv("DESK360_BASE_URL", "https://public-api.desk360.com")

# Render Postgres sometimes supplies postgres://; SQLAlchemy expects postgresql+psycopg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    phone = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=True)
    status = Column(String(32), nullable=False, default="PENDING_NAME")
    is_admin = Column(Boolean, nullable=False, default=False)
    invited_by_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(APP_TZ))
    approved_at = Column(DateTime(timezone=True), nullable=True)

class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)
    status = Column(String(20), nullable=False)  # COMING / NOT_COMING
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(APP_TZ))
    user = relationship("User")
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_user_date"),)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pro Clubs WhatsApp Bot")

def normalize_phone(value: str) -> str:
    return re.sub(r"\D", "", value or "")

def today_local():
    return datetime.now(APP_TZ).date()

def display_time(t):
    return t.strftime("%H:%M") if t else ""

async def desk360_send_text(to_phone: str, text: str):
    if not DESK360_API_KEY or not DESK360_INTEGRATION_ID:
        print(f"[DEV SEND] +{to_phone}: {text}")
        return {"dev": True}

    url = f"{DESK360_BASE_URL}/v1/integrations/{DESK360_INTEGRATION_ID}/conversations/messages"
    headers = {
        "Authorization": f"Bearer {DESK360_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"to": f"+{normalize_phone(to_phone)}", "text": text}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json() if r.content else {"ok": True}

def get_user(db, phone):
    return db.query(User).filter(User.phone == normalize_phone(phone)).first()

def get_or_create_admin(db):
    if not ADMIN_PHONE:
        return
    user = get_user(db, ADMIN_PHONE)
    if not user:
        user = User(
            phone=ADMIN_PHONE, name="Akın", status="ACTIVE",
            is_admin=True, invited_by_admin=True,
            approved_at=datetime.now(APP_TZ)
        )
        db.add(user)
    else:
        user.is_admin = True
        user.status = "ACTIVE"
    db.commit()

def active_members(db):
    return db.query(User).filter(User.status == "ACTIVE").order_by(User.name.asc()).all()

def pending_members(db):
    return db.query(User).filter(User.status == "PENDING_APPROVAL").order_by(User.id.asc()).all()

def help_text(is_admin=False):
    txt = (
        "🎮 *PRO CLUBS KOMUTLARI*\n\n"
        "*KAYIT* — Üyelik başlat\n"
        "*19:00* — Bu akşam geliş saatin\n"
        "*19:00-23:00* — Oynayacağın saat aralığı\n"
        "*YOKUM* — Bu akşam gelmiyorum\n"
        "*LISTE* — Bu akşamın katılım listesi\n"
        "*KADRO* — Kayıtlı oyuncular\n"
        "*DURUM* — Kendi bugünkü durumun\n"
        "*SİL* — Bugünkü cevabını kaldır\n"
        "*YARDIM* — Komutları göster"
    )
    if is_admin:
        txt += (
            "\n\n👑 *ADMIN*\n"
            "*BEKLEYEN* — Onay bekleyenler\n"
            "*ONAY 12* — Üyeyi onayla\n"
            "*RED 12* — Üyeyi reddet\n"
            "*DAVET 905xxxxxxxxx İsim* — Oyuncuyu davetli kaydet ve davet mesajı dene\n"
            "*DAVETLER* — Bekleyen davetler\n"
            "*ÜYELER* — Tüm üyeler ve durumları\n"
            "*PASIF 12* — Üyeyi pasife al\n"
            "*KADRO DETAY* — Admin detaylı kadro"
        )
    return txt

def parse_time_input(text):
    text = text.strip().replace(" ", "")
    m = re.fullmatch(r"([01]?\d|2[0-3])[:.]([0-5]\d)", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return ("single", datetime.strptime(f"{h:02d}:{mi:02d}", "%H:%M").time(), None)

    m = re.fullmatch(
        r"([01]?\d|2[0-3])[:.]([0-5]\d)-([01]?\d|2[0-3])[:.]([0-5]\d)",
        text
    )
    if m:
        h1, m1, h2, m2 = map(int, m.groups())
        return (
            "range",
            datetime.strptime(f"{h1:02d}:{m1:02d}", "%H:%M").time(),
            datetime.strptime(f"{h2:02d}:{m2:02d}", "%H:%M").time()
        )
    return None

def upsert_attendance(db, user, status, start=None, end=None):
    d = today_local()
    row = db.query(Attendance).filter(
        Attendance.user_id == user.id,
        Attendance.date == d
    ).first()
    if not row:
        row = Attendance(user_id=user.id, date=d, status=status)
        db.add(row)
    row.status = status
    row.start_time = start
    row.end_time = end
    row.updated_at = datetime.now(APP_TZ)
    db.commit()
    return row

def attendance_list_text(db):
    members = active_members(db)
    total = len(members)
    rows = (
        db.query(Attendance)
        .join(User)
        .filter(Attendance.date == today_local(), User.status == "ACTIVE")
        .order_by(Attendance.start_time.asc().nullslast(), User.name.asc())
        .all()
    )
    answered = len(rows)
    coming = [r for r in rows if r.status == "COMING"]
    not_coming = [r for r in rows if r.status == "NOT_COMING"]

    title = datetime.now(APP_TZ).strftime("%d.%m.%Y")
    lines = [f"🎮 *PRO CLUBS — {title}*", ""]
    if coming:
        for r in coming:
            if r.end_time:
                when = f"{display_time(r.start_time)}–{display_time(r.end_time)}"
            else:
                when = display_time(r.start_time)
            lines.append(f"✅ {r.user.name} — {when}")
    else:
        lines.append("Henüz geleceğini bildiren yok.")

    if not_coming:
        lines += ["", "❌ *Yokum diyenler*"]
        lines += [f"• {r.user.name}" for r in not_coming]

    lines += [
        "",
        f"👥 Cevap veren: *{answered}/{total}*",
        f"✅ Gelen: *{len(coming)}*",
        f"❌ Gelmeyen: *{len(not_coming)}*",
        f"❔ Cevap vermeyen: *{max(total-answered, 0)}*",
    ]
    return "\n".join(lines)

def squad_text(db, detail=False):
    members = active_members(db)
    if not members:
        return "👥 Henüz aktif oyuncu bulunmuyor."
    lines = ["👥 *PRO CLUBS KADROSU*", ""]
    for i, u in enumerate(members, 1):
        if detail:
            lines.append(f"{i}. {u.name} — +{u.phone} — ID:{u.id}")
        else:
            lines.append(f"{i}. {u.name}")
    lines += ["", f"Toplam kayıtlı oyuncu: *{len(members)}*"]
    return "\n".join(lines)

async def process_message(phone: str, text: str):
    phone = normalize_phone(phone)
    raw = (text or "").strip()
    cmd = raw.upper()

    db = SessionLocal()
    try:
        get_or_create_admin(db)
        user = get_user(db, phone)

        # Admin-only commands
        if user and user.is_admin:
            if cmd == "BEKLEYEN":
                pending = pending_members(db)
                if not pending:
                    return "✅ Onay bekleyen kullanıcı yok."
                return "🕐 *ONAY BEKLEYENLER*\n\n" + "\n".join(
                    f"ID:{u.id} — {u.name} — +{u.phone}" for u in pending
                )

            m = re.fullmatch(r"ONAY\s+(\d+)", cmd)
            if m:
                target = db.get(User, int(m.group(1)))
                if not target:
                    return "❌ Kullanıcı bulunamadı."
                target.status = "ACTIVE"
                target.approved_at = datetime.now(APP_TZ)
                db.commit()
                await desk360_send_text(target.phone, "✅ Kaydın onaylandı. Artık saat yazarak bu akşamki listeye katılabilirsin. Örnek: 19:00")
                return f"✅ {target.name} onaylandı."

            m = re.fullmatch(r"RED\s+(\d+)", cmd)
            if m:
                target = db.get(User, int(m.group(1)))
                if not target:
                    return "❌ Kullanıcı bulunamadı."
                target.status = "REJECTED"
                db.commit()
                await desk360_send_text(target.phone, "❌ Üyelik talebin admin tarafından onaylanmadı.")
                return f"✅ {target.name} reddedildi."

            m = re.match(r"^DAVET\s+(\+?\d{10,15})\s+(.+)$", raw, re.I)
            if m:
                invited_phone = normalize_phone(m.group(1))
                invited_name = m.group(2).strip()
                target = get_user(db, invited_phone)
                if not target:
                    target = User(
                        phone=invited_phone, name=invited_name,
                        status="INVITED", invited_by_admin=True
                    )
                    db.add(target)
                else:
                    target.name = invited_name
                    target.status = "INVITED"
                    target.invited_by_admin = True
                db.commit()
                try:
                    await desk360_send_text(
                        invited_phone,
                        f"🎮 Merhaba {invited_name}, Pro Clubs kadrosuna davet edildin. "
                        "Katılmak için bu WhatsApp hattına KAYIT yaz."
                    )
                    return f"✅ {invited_name} davetli olarak kaydedildi ve mesaj gönderimi denendi."
                except Exception as exc:
                    return (
                        f"✅ {invited_name} davetli olarak kaydedildi.\n"
                        "⚠️ İlk WhatsApp mesajı gönderilemedi. Yeni numaraya ilk mesaj için "
                        "Desk360'dan onaylı template gönderilmesi gerekir.\n"
                        f"Teknik hata: {type(exc).__name__}"
                    )

            if cmd == "DAVETLER":
                rows = db.query(User).filter(User.status == "INVITED").order_by(User.id.asc()).all()
                if not rows:
                    return "✅ Bekleyen davet yok."
                return "✉️ *BEKLEYEN DAVETLER*\n\n" + "\n".join(
                    f"ID:{u.id} — {u.name} — +{u.phone}" for u in rows
                )

            if cmd == "ÜYELER":
                rows = db.query(User).order_by(User.id.asc()).all()
                return "👥 *TÜM ÜYELER*\n\n" + "\n".join(
                    f"ID:{u.id} — {u.name or '-'} — {u.status} — +{u.phone}" for u in rows
                )

            m = re.fullmatch(r"PASIF\s+(\d+)", cmd)
            if m:
                target = db.get(User, int(m.group(1)))
                if not target:
                    return "❌ Kullanıcı bulunamadı."
                if target.is_admin:
                    return "⛔ Admin hesabı pasife alınamaz."
                target.status = "INACTIVE"
                db.commit()
                return f"✅ {target.name} pasife alındı."

            if cmd == "KADRO DETAY":
                return squad_text(db, detail=True)

        # Registration flow
        if not user:
            if cmd == "KAYIT":
                user = User(phone=phone, status="PENDING_NAME")
                db.add(user)
                db.commit()
                return "📝 Kayıt için adını yaz."
            return "⛔ Henüz kayıtlı değilsin. Kayıt olmak için *KAYIT* yaz."

        if user.status == "INVITED":
            if cmd == "KAYIT":
                user.status = "ACTIVE"
                user.approved_at = datetime.now(APP_TZ)
                db.commit()
                return f"✅ Hoş geldin {user.name}! Davetin doğrulandı ve üyeliğin aktif edildi.\n\nSaatini yazabilirsin: *19:00* veya *19:00-23:00*"
            return "✉️ Admin tarafından davet edilmişsin. Üyeliğini aktifleştirmek için *KAYIT* yaz."

        if user.status == "PENDING_NAME":
            if cmd == "KAYIT":
                return "📝 Şimdi adını yaz."
            if len(raw) < 2 or len(raw) > 100:
                return "❌ Geçerli bir isim yaz."
            user.name = raw
            user.status = "PENDING_APPROVAL"
            db.commit()
            if ADMIN_PHONE:
                await desk360_send_text(
                    ADMIN_PHONE,
                    f"🆕 *YENİ ÜYELİK TALEBİ*\n\n{user.name}\n+{user.phone}\nID: {user.id}\n\nOnay: *ONAY {user.id}*\nRed: *RED {user.id}*"
                )
            return "✅ Kayıt talebin alındı. Admin onayından sonra listeye katılabilirsin."

        if user.status == "PENDING_APPROVAL":
            return "🕐 Üyelik talebin admin onayı bekliyor."

        if user.status != "ACTIVE":
            return "⛔ Üyeliğin aktif değil. Admin ile iletişime geç."

        # Normal commands
        if cmd == "YARDIM":
            return help_text(user.is_admin)

        if cmd == "KADRO":
            return squad_text(db)

        if cmd == "LISTE":
            return attendance_list_text(db)

        if cmd == "YOKUM":
            upsert_attendance(db, user, "NOT_COMING")
            total_answered = db.query(Attendance).filter(Attendance.date == today_local()).count()
            return f"❌ {user.name} — bu akşam yok olarak işaretlendin.\n👥 Şu ana kadar cevap veren: *{total_answered} kişi*"

        if cmd == "DURUM":
            row = db.query(Attendance).filter(
                Attendance.user_id == user.id,
                Attendance.date == today_local()
            ).first()
            if not row:
                return "ℹ️ Bugün için henüz cevap vermedin."
            if row.status == "NOT_COMING":
                return f"❌ {user.name} — bu akşam yoksun."
            if row.end_time:
                return f"✅ {user.name} — {display_time(row.start_time)}–{display_time(row.end_time)}"
            return f"✅ {user.name} — {display_time(row.start_time)}"

        if cmd == "SİL":
            row = db.query(Attendance).filter(
                Attendance.user_id == user.id,
                Attendance.date == today_local()
            ).first()
            if not row:
                return "ℹ️ Silinecek bugünkü kayıt bulunamadı."
            db.delete(row)
            db.commit()
            return "🗑️ Bugünkü cevabın silindi."

        parsed = parse_time_input(raw)
        if parsed:
            _, start, end = parsed
            upsert_attendance(db, user, "COMING", start, end)
            total_answered = db.query(Attendance).filter(Attendance.date == today_local()).count()
            if end:
                return f"✅ {user.name} — bugün *{display_time(start)}–{display_time(end)}* arası listeye eklendin.\n👥 Şu ana kadar cevap veren: *{total_answered} kişi*"
            return f"✅ {user.name} — bugün *{display_time(start)}* olarak listeye eklendin.\n👥 Şu ana kadar cevap veren: *{total_answered} kişi*"

        return help_text(user.is_admin)
    finally:
        db.close()

@app.get("/")
def root():
    return {"service": "proclubs-whatsapp-bot", "status": "ok"}

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(APP_TZ).isoformat()}

@app.post("/webhook")
async def webhook(request: Request, authorization: str | None = Header(default=None), x_webhook_token: str | None = Header(default=None)):
    # Desk360 lets you define a token. Header naming can differ by integration,
    # so this check is optional until the exact incoming header is observed.
    if WEBHOOK_TOKEN and x_webhook_token and x_webhook_token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    payload = await request.json()
    msg = payload.get("messages") or payload.get("body", {}).get("messages") or {}
    phone = normalize_phone(str(msg.get("from", "")))
    text = msg.get("text", "")

    if not phone or not isinstance(text, str):
        return {"ok": True, "ignored": True}

    response_text = await process_message(phone, text)
    await desk360_send_text(phone, response_text)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
