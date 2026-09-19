import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date
import os
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ATTENDANCE_SHEET = "Davomat"
EMPLOYEES_SHEET  = "Ishchilar"

_spreadsheet = None


def get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        creds = Credentials.from_service_account_file(
            os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
            scopes=SCOPES,
        )
        client = gspread.authorize(creds)
        _spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_ID"))
    return _spreadsheet


def get_or_create_sheet(title: str, headers: list[str]):
    sp = get_spreadsheet()
    try:
        ws = sp.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sp.add_worksheet(title=title, rows=2000, cols=len(headers))
        ws.append_row(headers)
        ws.format("1", {"textFormat": {"bold": True}})
    return ws


# ─── DAVOMAT ──────────────────────────────────────────────────────────────────

def save_attendance(telegram_id: int, full_name: str, action: str) -> None:
    ws = get_or_create_sheet(
        ATTENDANCE_SHEET,
        ["Telegram ID", "Ism Familiya", "Holat", "Sana", "Vaqt"],
    )
    now = datetime.now()
    ws.append_row([
        str(telegram_id),
        full_name,
        action,
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
    ])


def get_today_attendance() -> list[dict]:
    ws = get_or_create_sheet(
        ATTENDANCE_SHEET,
        ["Telegram ID", "Ism Familiya", "Holat", "Sana", "Vaqt"],
    )
    today = date.today().strftime("%Y-%m-%d")
    records = ws.get_all_records()
    return [r for r in records if r.get("Sana") == today]


def has_action_today(telegram_id: int, action: str) -> bool:
    """Bugun ushbu amal allaqachon bajarilganligini tekshiradi."""
    records = get_today_attendance()
    for r in records:
        if str(r.get("Telegram ID")) == str(telegram_id) and r.get("Holat") == action:
            return True
    return False


# ─── ISHCHILAR ────────────────────────────────────────────────────────────────

def save_employee(data: dict) -> bool:
    ws = get_or_create_sheet(
        EMPLOYEES_SHEET,
        ["Telegram ID", "Ism", "Familiya", "Lavozim", "Telefon", "Ro'yxatdan o'tgan sana"],
    )
    records = ws.get_all_records()
    for row in records:
        if str(row.get("Telegram ID")) == str(data["telegram_id"]):
            return False

    ws.append_row([
        str(data["telegram_id"]),
        data["first_name"],
        data["last_name"],
        data["position"],
        data["phone"],
        datetime.now().strftime("%Y-%m-%d"),
    ])
    return True


def get_all_employee_ids() -> list[int]:
    ws = get_or_create_sheet(
        EMPLOYEES_SHEET,
        ["Telegram ID", "Ism", "Familiya", "Lavozim", "Telefon", "Ro'yxatdan o'tgan sana"],
    )
    records = ws.get_all_records()
    return [int(r["Telegram ID"]) for r in records if r.get("Telegram ID")]


def get_employee_name(telegram_id: int) -> str | None:
    ws = get_or_create_sheet(
        EMPLOYEES_SHEET,
        ["Telegram ID", "Ism", "Familiya", "Lavozim", "Telefon", "Ro'yxatdan o'tgan sana"],
    )
    records = ws.get_all_records()
    for row in records:
        if str(row.get("Telegram ID")) == str(telegram_id):
            return f"{row['Ism']} {row['Familiya']}"
    return None


def get_all_employees() -> list[dict]:
    ws = get_or_create_sheet(
        EMPLOYEES_SHEET,
        ["Telegram ID", "Ism", "Familiya", "Lavozim", "Telefon", "Ro'yxatdan o'tgan sana"],
    )
    return ws.get_all_records()
