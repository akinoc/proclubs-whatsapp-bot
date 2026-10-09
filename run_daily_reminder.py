"""Render Cron Job entrypoint. Set REMINDER_SERVICE_URL and REMINDER_CRON_SECRET."""
import os
from urllib.request import Request, urlopen

base = os.environ["REMINDER_SERVICE_URL"].rstrip("/")
secret = os.environ["REMINDER_CRON_SECRET"]
request = Request(
    base + "/jobs/daily-reminder",
    data=b"{}",
    headers={"X-Cron-Secret": secret, "Content-Type": "application/json"},
    method="POST",
)
with urlopen(request, timeout=120) as response:
    print(response.read().decode("utf-8"))
