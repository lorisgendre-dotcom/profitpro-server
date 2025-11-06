import gspread
from google.oauth2.service_account import Credentials
from utils.logger import get_logger
from config import settings

log = get_logger("sheets", settings.LOG_LEVEL)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _client():
    creds = Credentials.from_service_account_file(settings.GOOGLE_SA_PATH, scopes=_SCOPES)
    return gspread.authorize(creds)

def append_row(values: list):
    try:
        gc = _client()
        sh = gc.open(settings.GDRIVE_SHEET_NAME)
        ws = sh.sheet1
        ws.append_row(values, value_input_option="USER_ENTERED")
        log.info("Google Sheets: ligne ajoutée.")
    except Exception as e:
        log.exception("Google Sheets erreur: %s", e)