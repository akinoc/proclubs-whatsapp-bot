
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
