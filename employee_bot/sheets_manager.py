import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ATTENDANCE_SHEET = "Davomat"
EMPLOYEES_SHEET = "Ishchilar"


def get_client():
    creds = Credentials.from_service_account_file(
        os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


def get_or_create_sheet(spreadsheet, title: str, headers: list[str]):
    try:
        sheet = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
        sheet.append_row(headers)
    return sheet


def save_attendance(telegram_id: int, full_name: str, action: str):
    """action: 'Keldi' yoki 'Ketdi'"""
    client = get_client()
    spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_ID"))
    sheet = get_or_create_sheet(
        spreadsheet,
        ATTENDANCE_SHEET,
        ["Telegram ID", "Ism Familiya", "Holat", "Sana", "Vaqt"],
    )
    now = datetime.now()
    sheet.append_row([
        str(telegram_id),
        full_name,
        action,
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
    ])


def save_employee(data: dict):
    """Yangi ishchi ma'lumotlarini saqlash"""
    client = get_client()
    spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_ID"))
    sheet = get_or_create_sheet(
        spreadsheet,
        EMPLOYEES_SHEET,
        ["Telegram ID", "Ism", "Familiya", "Lavozim", "Telefon", "Ro'yxatdan o'tgan sana"],
    )

    # Takroriy ro'yxatdan o'tishni oldini olish
    records = sheet.get_all_records()
    for row in records:
        if str(row.get("Telegram ID")) == str(data["telegram_id"]):
            return False  # Allaqachon ro'yxatdan o'tgan

    sheet.append_row([
        str(data["telegram_id"]),
        data["first_name"],
        data["last_name"],
        data["position"],
        data["phone"],
        datetime.now().strftime("%Y-%m-%d"),
    ])
    return True


def get_all_employee_ids() -> list[int]:
    """Barcha ro'yxatdagi ishchilarning Telegram ID larini qaytaradi"""
    client = get_client()
    spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_ID"))
    try:
        sheet = spreadsheet.worksheet(EMPLOYEES_SHEET)
        records = sheet.get_all_records()
        return [int(r["Telegram ID"]) for r in records if r.get("Telegram ID")]
    except gspread.WorksheetNotFound:
        return []


def get_employee_name(telegram_id: int) -> str | None:
    """Ishchi ismini qaytaradi"""
    client = get_client()
    spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_ID"))
    try:
        sheet = spreadsheet.worksheet(EMPLOYEES_SHEET)
        records = sheet.get_all_records()
        for row in records:
            if str(row.get("Telegram ID")) == str(telegram_id):
                return f"{row['Ism']} {row['Familiya']}"
    except gspread.WorksheetNotFound:
        pass
    return None


def get_all_employees() -> list[dict]:
    """Barcha ishchilar ro'yxatini qaytaradi"""
    client = get_client()
    spreadsheet = client.open_by_key(os.getenv("SPREADSHEET_ID"))
    try:
        sheet = spreadsheet.worksheet(EMPLOYEES_SHEET)
        return sheet.get_all_records()
    except gspread.WorksheetNotFound:
        return []
