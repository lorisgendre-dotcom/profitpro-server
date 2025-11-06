import requests
from utils.logger import get_logger
from config import settings

log = get_logger("telegram", settings.LOG_LEVEL)

def send_message(text: str) -> None:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.warning("Telegram non configuré, message ignoré.")
        return
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": settings.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code != 200:
            log.error("Telegram error %s: %s", r.status_code, r.text)
    except Exception as e:
        log.exception("Erreur Telegram: %s", e)