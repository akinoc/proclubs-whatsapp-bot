# Pro Clubs WhatsApp Bot — Meta Cloud API

Desk360 yoktur. Doğrudan Meta WhatsApp Cloud API kullanır.

## Render Environment
DATABASE_URL
ADMIN_PHONE
META_ACCESS_TOKEN
META_PHONE_NUMBER_ID
META_WABA_ID
META_VERIFY_TOKEN
META_GRAPH_VERSION
TIMEZONE

Opsiyonel:
META_INVITE_TEMPLATE_NAME
META_INVITE_TEMPLATE_LANG

## Render
Build:
pip install -r requirements.txt

Start:
uvicorn app:app --host 0.0.0.0 --port $PORT

Health:
https://YOUR-SERVICE.onrender.com/health

Webhook:
https://YOUR-SERVICE.onrender.com/webhook

Meta webhook doğrulamasında Verify Token, Render'daki META_VERIFY_TOKEN ile birebir aynı olmalıdır.
Webhook alanlarından `messages` subscribe edilmelidir.

## Komutlar
KAYIT
19:00
19:00-23:00
YOKUM
LISTE
KADRO
DURUM
SİL
YARDIM

Admin:
BEKLEYEN
ONAY 12
RED 12
DAVET 905xxxxxxxxx İsim
DAVETLER
ÜYELER
PASIF 12
KADRO DETAY

## DAVET
Yeni numaraya işletme tarafından başlatılan mesaj için onaylı WhatsApp template gerekir.
Meta WhatsApp Manager'da örneğin `proclubs_invite` adlı template oluştur:
Merhaba {{1}}, Pro Clubs kadrosuna davet edildin. Katılmak için KAYIT yaz.

Sonra Render:
META_INVITE_TEMPLATE_NAME=proclubs_invite
META_INVITE_TEMPLATE_LANG=tr

Davet edilen kişi KAYIT yazınca admin onayı olmadan ACTIVE olur.
