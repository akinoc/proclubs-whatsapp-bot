"""Run ONE time from Render after the WABA billing method is active."""
import os
from urllib.request import Request, urlopen

base = os.environ["REMINDER_SERVICE_URL"].rstrip("/")
secret = os.environ["REMINDER_CRON_SECRET"]
request = Request(
    base + "/jobs/retry-payment-fixed", data=b"{}", method="POST",
    headers={"X-Cron-Secret": secret, "X-Retry-Confirm": "PAYMENT_FIXED_20261009", "Content-Type": "application/json"},
)
with urlopen(request, timeout=120) as response:
    print(response.read().decode("utf-8"))
