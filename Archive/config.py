import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-moi")

    ZMQ_PUSH_ADDR = os.getenv("ZMQ_PUSH_ADDR", "tcp://127.0.0.1:5555")
    ZMQ_PULL_ADDR = os.getenv("ZMQ_PULL_ADDR", "tcp://127.0.0.1:5556")

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    GOOGLE_SA_PATH = os.getenv("GOOGLE_SA_PATH", "./data/service_account.json")
    GDRIVE_SHEET_NAME = os.getenv("GDRIVE_SHEET_NAME", "Formation harmonique Dow Jones")

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "US30")
    DEFAULT_LOT = float(os.getenv("DEFAULT_LOT", "0.10"))
    SLIPPAGE_POINTS = int(os.getenv("SLIPPAGE_POINTS", "20"))

settings = Settings()