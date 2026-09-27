# Pro Clubs WhatsApp Bot

Desk360 Public API + FastAPI + PostgreSQL için hazırlanmıştır.

## Kullanıcı komutları
- KAYIT
- 19:00
- 19:00-23:00
- YOKUM
- LISTE
- KADRO
- DURUM
- SİL
- YARDIM

## Admin komutları
- BEKLEYEN
- ONAY 12
- RED 12
- DAVET 905xxxxxxxxx İsim
- DAVETLER
- ÜYELER
- PASIF 12
- KADRO DETAY

## Render
Build command:
    pip install -r requirements.txt

Start command:
    uvicorn app:app --host 0.0.0.0 --port $PORT

Health:
    https://YOUR-SERVICE.onrender.com/health

Webhook:
    https://YOUR-SERVICE.onrender.com/webhook

## Environment variables
DATABASE_URL
ADMIN_PHONE
DESK360_API_KEY
DESK360_INTEGRATION_ID
WEBHOOK_TOKEN
TIMEZONE=Europe/Istanbul

## Desk360 Public API
Gelen webhook örneği:
{
  "messages": {
    "id": "...",
    "text": "LISTE",
    "from": "90xxxxxxxxxx",
    "timestamp": "...",
    "name": "John Doe",
    "type": "text"
  }
}

Metin gönderme endpoint'i:
POST https://public-api.desk360.com/v1/integrations/{integrationId}/conversations/messages
Authorization: Bearer <API_KEY>

## DAVET hakkında önemli not
Kod DAVET komutunda kişiyi INVITED olarak veritabanına kaydeder ve standart mesaj gönderimini dener.
WhatsApp'ta yeni bir numaraya işletme tarafından başlatılan ilk mesaj, 24 saatlik pencere yoksa onaylı template
olmak zorundadır. Desk360 panelinden onaylı davet template'i oluşturulmalıdır. Public API'nin template gönderme
endpoint'i Desk360'ın açık dokümanında netleştiğinde bu fonksiyon doğrudan API template çağrısına bağlanabilir.
Davetli kişi KAYIT yazınca admin onayı gerekmeksizin ACTIVE olur.
