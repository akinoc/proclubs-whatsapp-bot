
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
