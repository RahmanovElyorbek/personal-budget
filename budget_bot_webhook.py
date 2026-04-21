"""
💰 Oson Byudjet Telegram Bot — v3
=======================================================
- Supabase PostgreSQL database
- 7 kunlik bepul sinov
- To'lov tizimi
- Qarzlar ro'yxati
- Balanslar nazorati (tranzaksiya bilan bog'langan!)
- Ovoz orqali kiritish (OpenAI Whisper)
- PDF hisobot
"""

import logging
import os
import asyncio
import asyncpg
import tempfile
import httpx
import io
from datetime import datetime, timedelta, date
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# ===================== SOZLAMALAR =====================
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "")
PORT           = int(os.environ.get("PORT", 8080))
DATABASE_URL   = os.environ.get("DATABASE_URL", "")
ADMIN_ID       = int(os.environ.get("ADMIN_ID", "8008645253"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

PRICE_MONTHLY   = 25000
PRICE_QUARTERLY = 60000
PRICE_YEARLY    = 199000

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================== KATEGORIYALAR =====================
EXPENSE_CATEGORIES = [
    "🍔 Oziq-ovqat", "🚌 Transport", "🏠 Uy-joy", "💊 Salomatlik",
    "🎮 Ko'ngil ochar", "👗 Kiyim-kechak", "📚 Ta'lim", "💡 Kommunal",
    "📱 Aloqa", "🎁 Sovg'alar", "🏋️ Sport", "✈️ Sayohat", "📦 Boshqa"
]
INCOME_CATEGORIES = [
    "💼 Maosh", "💻 Freelance", "📈 Investitsiya", "🎁 Sovg'a",
    "🏦 Bank foizi", "🛒 Sotish", "📦 Boshqa daromad"
]

BALANCE_TYPES = {
    "cash":  "💵 Naqd pul",
    "card":  "💳 Karta",
    "bank":  "🏦 Bank hisobi",
    "other": "📦 Boshqa",
}

MONTH_NAMES = {
    1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
    5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
    9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
}

# ===================== OVOZ TANISH =====================

async def transcribe_voice(file_path: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("voice.ogg", f, "audio/ogg")},
                    data={"model": "whisper-1"}
                )
            if response.status_code == 200:
                return response.json().get("text", "")
            else:
                logger.error(f"Whisper error: {response.text}")
                return ""
    except Exception as e:
        logger.error(f"Transcribe error: {e}")
        return ""

async def parse_voice_transaction(text: str) -> dict:
    import re
    text_lower = text.lower()

    word_numbers = {
        "bir": 1, "ikki": 2, "uch": 3, "to'rt": 4, "besh": 5,
        "olti": 6, "yetti": 7, "sakkiz": 8, "to'qqiz": 9, "o'n": 10,
        "yigirma": 20, "o'ttiz": 30, "qirq": 40, "ellik": 50,
        "oltmish": 60, "yetmish": 70, "sakson": 80, "to'qson": 90,
        "yuz": 100, "ming": 1000, "million": 1000000,
        "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6,
        "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10, "bin": 1000,
        "milyon": 1000000, "milisom": 1000000,
    }

    numbers = re.findall(r'\d+(?:[.,]\d+)?', text.replace(" ", ""))
    amount = 0
    for n in numbers:
        val = float(n.replace(",", ".").replace(".", ""))
        if val >= 100:
            amount = val
            break
        elif val > 0 and amount == 0:
            amount = val

    if amount == 0:
        words = text_lower.split()
        for i, word in enumerate(words):
            clean_word = word.strip(".,!?")
            if clean_word in word_numbers:
                base = word_numbers[clean_word]
                if i + 1 < len(words):
                    next_word = words[i+1].strip(".,!?")
                    if next_word in ("ming", "bin"):
                        amount = base * 1000
                        break
                    elif next_word in ("million", "milyon", "milisom"):
                        amount = base * 1000000
                        break
                if amount == 0:
                    amount = base

    income_words = ["maosh", "daromad", "oldim", "tushdi", "kirdi", "topdi"]
    txn_type = "expense"
    for w in income_words:
        if w in text_lower:
            txn_type = "income"
            break

    category_map = {
        "oziq": "🍔 Oziq-ovqat", "ovqat": "🍔 Oziq-ovqat", "non": "🍔 Oziq-ovqat",
        "go'sht": "🍔 Oziq-ovqat", "bozor": "🍔 Oziq-ovqat",
        "transport": "🚌 Transport", "taksi": "🚌 Transport", "avtobus": "🚌 Transport",
        "benzin": "🚌 Transport", "mashina": "🚌 Transport",
        "uy": "🏠 Uy-joy", "ijara": "🏠 Uy-joy",
        "dori": "💊 Salomatlik", "shifokor": "💊 Salomatlik",
        "kiyim": "👗 Kiyim-kechak",
        "telefon": "📱 Aloqa", "internet": "📱 Aloqa",
        "kommunal": "💡 Kommunal", "gaz": "💡 Kommunal",
        "maosh": "💼 Maosh", "oylik": "💼 Maosh",
    }

    category = "📦 Boshqa"
    if txn_type == "income":
        category = "📦 Boshqa daromad"
    for key, cat in category_map.items():
        if key in text_lower:
            category = cat
            break

    return {"type": txn_type, "amount": amount, "category": category, "text": text}

# ===================== DATABASE =====================
db_pool = None

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id   BIGINT PRIMARY KEY,
                name          TEXT DEFAULT '',
                budget        NUMERIC DEFAULT 0,
                registered_at TIMESTAMP DEFAULT NOW(),
                premium_until TIMESTAMP DEFAULT NULL,
                is_premium    BOOLEAN DEFAULT FALSE
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS balances (
                id          SERIAL PRIMARY KEY,
                telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                type        TEXT NOT NULL,
                amount      NUMERIC DEFAULT 0,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          SERIAL PRIMARY KEY,
                telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
                type        TEXT NOT NULL,
                amount      NUMERIC NOT NULL,
                category    TEXT DEFAULT 'Boshqa',
                note        TEXT DEFAULT '',
                balance_id  INTEGER REFERENCES balances(id) ON DELETE SET NULL,
                date        TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS debts (
                id          SERIAL PRIMARY KEY,
                telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
                person_name TEXT NOT NULL,
                amount      NUMERIC NOT NULL,
                direction   TEXT NOT NULL,
                due_date    DATE DEFAULT NULL,
                is_paid     BOOLEAN DEFAULT FALSE,
                note        TEXT DEFAULT '',
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)
    logger.info("✅ Database tayyor!")

async def is_new_user(telegram_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id FROM users WHERE telegram_id = $1", telegram_id
        )
        return row is None

async def ensure_user(telegram_id: int, name: str = ""):
    async with db_pool.acquire() as conn:
        # Foydalanuvchi yangi bo'lsa — user yaratamiz
        was_new = await conn.fetchrow(
            "SELECT telegram_id FROM users WHERE telegram_id = $1", telegram_id
        )
        await conn.execute("""
            INSERT INTO users (telegram_id, name, registered_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (telegram_id) DO UPDATE SET name = $2
        """, telegram_id, name)

        # Yangi foydalanuvchiga avtomatik 2 ta balans
        if was_new is None:
            await conn.execute("""
                INSERT INTO balances (telegram_id, name, type, amount)
                VALUES ($1, $2, $3, 0), ($1, $4, $5, 0)
            """, telegram_id, "Naqd", "cash", "Karta", "card")

async def is_user_premium(telegram_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT registered_at, premium_until, is_premium
            FROM users WHERE telegram_id = $1
        """, telegram_id)
        if not row:
            return False
        if row["is_premium"] and row["premium_until"]:
            if row["premium_until"] > datetime.now():
                return True
        trial_end = row["registered_at"] + timedelta(days=7)
        return datetime.now() < trial_end

async def get_trial_days_left(telegram_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT registered_at FROM users WHERE telegram_id = $1", telegram_id
        )
        if not row:
            return 0
        trial_end = row["registered_at"] + timedelta(days=7)
        delta = trial_end - datetime.now()
        return max(0, delta.days)

async def activate_premium(telegram_id: int, days: int):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET is_premium = TRUE,
                premium_until = NOW() + ($1 || ' days')::INTERVAL
            WHERE telegram_id = $2
        """, str(days), telegram_id)

async def get_budget(telegram_id: int) -> float:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT budget FROM users WHERE telegram_id = $1", telegram_id
        )
        return float(row["budget"]) if row else 0.0

async def set_budget(telegram_id: int, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET budget = $1 WHERE telegram_id = $2",
            amount, telegram_id
        )

async def add_transaction(telegram_id: int, txn_type: str,
                          amount: float, category: str, note: str,
                          balance_id: int = None):
    """Tranzaksiya qo'shish + balansni yangilash."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Tranzaksiyani saqlash
            await conn.execute("""
                INSERT INTO transactions (telegram_id, type, amount, category, note, balance_id)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, telegram_id, txn_type, amount, category, note, balance_id)

            # Balansni yangilash
            if balance_id:
                if txn_type == "income":
                    await conn.execute(
                        "UPDATE balances SET amount = amount + $1 WHERE id = $2",
                        amount, balance_id
                    )
                else:
                    await conn.execute(
                        "UPDATE balances SET amount = amount - $1 WHERE id = $2",
                        amount, balance_id
                    )

async def get_month_transactions(telegram_id: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT t.type, t.amount, t.category, t.note, t.date, b.name AS balance_name
            FROM transactions t
            LEFT JOIN balances b ON t.balance_id = b.id
            WHERE t.telegram_id = $1
              AND DATE_TRUNC('month', t.date) = DATE_TRUNC('month', NOW())
            ORDER BY t.date DESC
        """, telegram_id)
        return [dict(r) for r in rows]

async def get_transactions_by_month(telegram_id: int, year: int, month: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT t.type, t.amount, t.category, t.note, t.date, b.name AS balance_name
            FROM transactions t
            LEFT JOIN balances b ON t.balance_id = b.id
            WHERE t.telegram_id = $1
              AND EXTRACT(YEAR FROM t.date) = $2
              AND EXTRACT(MONTH FROM t.date) = $3
            ORDER BY t.date DESC
        """, telegram_id, year, month)
        return [dict(r) for r in rows]

async def get_available_months(telegram_id: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT
                EXTRACT(YEAR FROM date)::int AS year,
                EXTRACT(MONTH FROM date)::int AS month
            FROM transactions
            WHERE telegram_id = $1
              AND date >= NOW() - INTERVAL '6 months'
            ORDER BY year DESC, month DESC
        """, telegram_id)
        return [dict(r) for r in rows]

async def clear_month_transactions(telegram_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM transactions
            WHERE telegram_id = $1
              AND DATE_TRUNC('month', date) = DATE_TRUNC('month', NOW())
        """, telegram_id)

async def add_debt(telegram_id: int, person_name: str, amount: float,
                   direction: str, due_date=None, note: str = ""):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO debts (telegram_id, person_name, amount, direction, due_date, note)
            VALUES ($1, $2, $3, $4, $5, $6)
        """, telegram_id, person_name, amount, direction, due_date, note)

async def get_debts(telegram_id: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, person_name, amount, direction, due_date, is_paid, note, created_at
            FROM debts
            WHERE telegram_id = $1 AND is_paid = FALSE
            ORDER BY due_date ASC NULLS LAST, created_at DESC
        """, telegram_id)
        return [dict(r) for r in rows]

async def mark_debt_paid(debt_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE debts SET is_paid = TRUE WHERE id = $1", debt_id
        )

async def check_due_debts(telegram_id: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, person_name, amount, direction, due_date
            FROM debts
            WHERE telegram_id = $1
              AND is_paid = FALSE
              AND due_date = CURRENT_DATE
        """, telegram_id)
        return [dict(r) for r in rows]

async def get_balances(telegram_id: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, name, type, amount
            FROM balances
            WHERE telegram_id = $1
            ORDER BY created_at ASC
        """, telegram_id)
        return [dict(r) for r in rows]

async def add_balance(telegram_id: int, name: str, bal_type: str, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO balances (telegram_id, name, type, amount)
            VALUES ($1, $2, $3, $4)
        """, telegram_id, name, bal_type, amount)

async def update_balance(balance_id: int, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE balances SET amount = $1 WHERE id = $2", amount, balance_id
        )

async def delete_balance(balance_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM balances WHERE id = $1", balance_id)

# ===================== PDF =====================

def generate_stats_pdf(user_name, stats, cat_stats, budget, month_str):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                           rightMargin=2*cm, leftMargin=2*cm,
                           topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'Title', parent=styles['Title'],
        fontSize=16, spaceAfter=20
    )
    normal_style = ParagraphStyle(
        'Normal', parent=styles['Normal'],
        fontSize=11, spaceAfter=6
    )

    elements = []
    elements.append(Paragraph(f"Oson Byudjet — Hisobot", title_style))
    elements.append(Paragraph(f"Foydalanuvchi: {user_name}", normal_style))
    elements.append(Paragraph(f"Davr: {month_str}", normal_style))
    elements.append(Spacer(1, 0.5*cm))

    main_data = [
        ["Ko'rsatkich", "Miqdor"],
        ["Jami daromad", f"{stats['income']:,.0f} so'm"],
        ["Jami xarajat", f"{stats['expenses']:,.0f} so'm"],
        ["Sof balans", f"{stats['balance']:,.0f} so'm"],
    ]

    if budget > 0:
        used_pct = int(stats['expenses'] / budget * 100) if budget else 0
        remaining = max(budget - stats['expenses'], 0)
        main_data.append(["Belgilangan budget", f"{budget:,.0f} so'm"])
        main_data.append(["Sarflangan", f"{stats['expenses']:,.0f} so'm ({used_pct}%)"])
        main_data.append(["Qolgan budget", f"{remaining:,.0f} so'm"])

    main_table = Table(main_data, colWidths=[9*cm, 8*cm])
    main_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2255A8')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('FONTSIZE', (0, 1), (-1, -1), 11),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4FF')]),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(main_table)
    elements.append(Spacer(1, 0.5*cm))

    if cat_stats:
        elements.append(Paragraph("Xarajatlar kategoriyalar bo'yicha:", normal_style))
        elements.append(Spacer(1, 0.3*cm))

        cat_data = [["Kategoriya", "Miqdor", "Foiz"]]
        total_exp = stats['expenses'] if stats['expenses'] > 0 else 1

        for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1]):
            pct = int(amt / total_exp * 100)
            cat_data.append([cat, f"{amt:,.0f} so'm", f"{pct}%"])

        cat_table = Table(cat_data, colWidths=[9*cm, 6*cm, 2*cm])
        cat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2255A8')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 0), (2, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4FF')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(cat_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

# ===================== YORDAMCHI =====================

def calc_stats(transactions: list) -> dict:
    income = expenses = 0
    for t in transactions:
        if t["type"] == "income":
            income += float(t["amount"])
        else:
            expenses += float(t["amount"])
    return {"income": income, "expenses": expenses,
            "balance": income - expenses, "transactions": transactions}

def format_money(amount: float) -> str:
    return f"{amount:,.0f} so'm"

# ===================== KLAVIATURALAR =====================

def main_keyboard(user_id=None):
    buttons = [
        [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
         InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
        [InlineKeyboardButton("📊 Statistika", callback_data="stats"),
         InlineKeyboardButton("💰 Budget belgilash", callback_data="set_budget")],
        [InlineKeyboardButton("📋 Tarix", callback_data="history"),
         InlineKeyboardButton("💸 Qarzlar", callback_data="debts")],
        [InlineKeyboardButton("💳 Balanslar", callback_data="balances"),
         InlineKeyboardButton("🗑️ Tozalash", callback_data="clear_month")],
    ]
    # Admin uchun qo'shimcha tugma
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(buttons)

def category_keyboard(categories, txn_type):
    buttons, row = [], []
    for i, cat in enumerate(categories):
        row.append(InlineKeyboardButton(cat, callback_data=f"cat_{txn_type}_{i}"))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def balance_select_keyboard(balances: list):
    """Tranzaksiya uchun balans tanlash klaviaturasi."""
    buttons = []
    for b in balances:
        type_emoji = BALANCE_TYPES.get(b["type"], "📦").split()[0]
        label = f"{type_emoji} {b['name']} — {format_money(float(b['amount']))}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"selbal_{b['id']}")])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Oylik — 25,000 so'm", callback_data="pay_monthly")],
        [InlineKeyboardButton("📆 3 oylik — 60,000 so'm", callback_data="pay_quarterly")],
        [InlineKeyboardButton("🗓 Yillik — 199,000 so'm", callback_data="pay_yearly")],
    ])

def history_months_keyboard(months: list):
    buttons, row = [], []
    for m in months:
        label = f"{MONTH_NAMES[m['month']]} {m['year']}"
        cb    = f"history_{m['year']}_{m['month']}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) == 2:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def debt_direction_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Men berdim (menga qaytarishi kerak)", callback_data="debt_dir_gave")],
        [InlineKeyboardButton("🟢 Men oldim (men qaytarishi kerak)", callback_data="debt_dir_took")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="debts")],
    ])

def balance_type_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Naqd pul", callback_data="bal_type_cash")],
        [InlineKeyboardButton("💳 Karta", callback_data="bal_type_card")],
        [InlineKeyboardButton("🏦 Bank hisobi", callback_data="bal_type_bank")],
        [InlineKeyboardButton("📦 Boshqa", callback_data="bal_type_other")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="balances")],
    ])

# ===================== TO'LOV =====================

async def show_payment_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⏰ <b>Sinov muddati tugadi!</b>\n\n"
        "Budget botdan foydalanishni davom ettirish uchun\n"
        "quyidagi tariflardan birini tanlang:\n\n"
        "📅 Oylik    — <b>25,000 so'm</b>\n"
        "📆 3 oylik  — <b>60,000 so'm</b>\n"
        "🗓 Yillik   — <b>199,000 so'm</b>\n"
    )
    if update.message:
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=payment_keyboard())
    else:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=payment_keyboard())

async def notify_admin_payment(context, user_id, user_name, plan, price):
    days_map = {"Oylik": 30, "3 Oylik": 90, "Yillik": 365}
    days = days_map.get(plan, 30)
    text = (
        f"💳 <b>Yangi to'lov so'rovi!</b>\n\n"
        f"👤 Foydalanuvchi: <b>{user_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📅 Tarif: <b>{plan}</b>\n"
        f"💰 Summa: <b>{price:,} so'm</b>\n\n"
        f"To'lovni qabul qiling va tasdiqlang:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"adm_confirm_{user_id}_{days}")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data=f"adm_reject_{user_id}")],
    ])
    await context.bot.send_message(
        chat_id=ADMIN_ID, text=text, parse_mode="HTML", reply_markup=markup
    )

# ===================== HANDLERLAR =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    is_new = await is_new_user(user.id)
    await ensure_user(user.id, user.first_name)

    due_debts = await check_due_debts(user.id)
    for debt in due_debts:
        direction = "menga qaytarishi" if debt["direction"] == "gave" else "men qaytarishim"
        await update.message.reply_text(
            f"🔔 <b>Qarz eslatmasi!</b>\n\n"
            f"👤 {debt['person_name']} — {format_money(float(debt['amount']))}\n"
            f"📅 Bugun {direction} kerak!",
            parse_mode="HTML"
        )

    if is_new:
        welcome_text = (
            f"👋 Salom, <b>{user.first_name}</b>! Xush kelibsiz!\n\n"
            f"💰 <b>Oson Byudjet</b> — shaxsiy moliya yordamchingiz!\n\n"
            f"Bu bot bilan:\n"
            f"✅ Daromad va xarajatlarni yozing\n"
            f"✅ Ovoz orqali kiritish 🎤\n"
            f"✅ Oylik statistika va PDF hisobot\n"
            f"✅ Byudjet belgilang va nazorat qiling\n"
            f"✅ Qarzlarni kuzating\n"
            f"✅ Balanslar (Naqd, Karta) bilan bog'langan!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎁 <b>7 kun to'liq BEPUL!</b>\n"
            f"💡 Sizga avtomatik <b>Naqd</b> va <b>Karta</b> balanslari yaratildi.\n"
            f"💳 Balanslar bo'limidan miqdorni kiriting va boshlang!\n\n"
            f"👇 Boshlash uchun quyidagi tugmani bosing!"
        )
        await update.message.reply_text(
            welcome_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🚀 Boshlash!", callback_data="back_main")
            ]])
        )
        return

    premium = await is_user_premium(user.id)
    if not premium:
        await show_payment_screen(update, context)
        return

    days_left = await get_trial_days_left(user.id)
    txns   = await get_month_transactions(user.id)
    stats  = calc_stats(txns)
    budget = await get_budget(user.id)
    bals   = await get_balances(user.id)

    trial_msg = ""
    if days_left > 0:
        trial_msg = f"🎁 Bepul sinov: <b>{days_left} kun qoldi</b>\n"

    text = (
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        f"💰 <b>Oson Byudjet</b>\n"
        f"📅 <b>{datetime.now().strftime('%B %Y')}</b>\n"
        f"{trial_msg}"
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 Daromad : <b>{format_money(stats['income'])}</b>\n"
        f"📤 Xarajat : <b>{format_money(stats['expenses'])}</b>\n"
        f"💵 Balans  : <b>{format_money(stats['balance'])}</b>\n"
    )
    if bals:
        total = sum(float(b["amount"]) for b in bals)
        text += f"💳 Jami balans: <b>{format_money(total)}</b>\n"
    if budget > 0:
        pct = min(int(stats["expenses"] / budget * 10), 10)
        bar = "🟥" * pct + "⬜" * (10 - pct)
        remaining = budget - stats["expenses"]
        text += (
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Budget : <b>{format_money(budget)}</b>\n"
            f"📊 {bar} {int(stats['expenses']/budget*100)}%\n"
            f"✅ Qolgan : <b>{format_money(max(remaining, 0))}</b>\n"
        )
    text += "\n🎤 Ovoz yuboring yoki tugma bosing:"
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=main_keyboard(user.id))

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    premium = await is_user_premium(user_id)
    if not premium:
        await show_payment_screen(update, context)
        return

    msg = await update.message.reply_text("🎤 Ovoz tanilmoqda...")

    voice = update.message.voice
    file  = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        text = await transcribe_voice(tmp.name)

    if not text:
        await msg.edit_text("❌ Ovozni tanib bo'lmadi. Qaytadan urinib ko'ring.")
        return

    parsed = await parse_voice_transaction(text)

    if parsed["amount"] <= 0:
        await msg.edit_text(
            f"🎤 <b>Tanildi:</b> {text}\n\n"
            f"❌ Miqdor aniqlanmadi.\n"
            f"<i>Masalan: 'Non uchun 5000'</i>",
            parse_mode="HTML"
        )
        return

    # Balanslarni olish
    bals = await get_balances(user_id)

    context.user_data["voice_parsed"] = parsed
    emoji = "📥" if parsed["type"] == "income" else "📤"
    type_text = "Daromad" if parsed["type"] == "income" else "Xarajat"

    await msg.edit_text(
        f"🎤 <b>Tanildi:</b> {text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} Tur: <b>{type_text}</b>\n"
        f"💰 Miqdor: <b>{format_money(parsed['amount'])}</b>\n"
        f"📁 Kategoriya: {parsed['category']}\n\n"
        f"Qaysi balansga?",
        parse_mode="HTML",
        reply_markup=balance_select_keyboard(bals)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Yordam — Oson Byudjet</b>\n\n"
        "/start — Bosh menyu\n/help — Yordam\n\n"
        "➕ Daromad/Xarajat kiritish\n"
        "🎤 Ovoz orqali kiritish\n"
        "💳 Balanslar bilan bog'langan\n"
        "📊 Statistika va PDF hisobot\n"
        "💸 Qarzlar ro'yxati\n"
        "🎯 Byudjet belgilash",
        parse_mode="HTML"
    )

async def admin_test_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun: kunlik eslatmani zudlik bilan test qilish."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Bu komanda faqat admin uchun.")
        return

    await update.message.reply_text(
        "🔔 <b>Kunlik eslatma test qilinmoqda...</b>\n\n"
        "Barcha foydalanuvchilarga (bugun kiritmaganlariga) eslatma yuboriladi.",
        parse_mode="HTML"
    )

    await send_daily_reminders(context.bot)

    await update.message.reply_text(
        "✅ <b>Test tugadi!</b>\n\n"
        "Natijalarni Render logs'dan ko'ring:\n"
        "<code>✅ Eslatmalar yuborildi: X ta</code>",
        parse_mode="HTML"
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun: umumiy statistika."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Bu komanda faqat admin uchun.")
        return

    async with db_pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        premium_users = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE is_premium = TRUE AND premium_until > NOW()"
        )
        today_active = await conn.fetchval("""
            SELECT COUNT(DISTINCT telegram_id) FROM transactions
            WHERE DATE(date AT TIME ZONE 'Asia/Tashkent') = CURRENT_DATE
        """)
        week_active = await conn.fetchval("""
            SELECT COUNT(DISTINCT telegram_id) FROM transactions
            WHERE date >= NOW() - INTERVAL '7 days'
        """)
        total_txns = await conn.fetchval("SELECT COUNT(*) FROM transactions")

    msg = (
        f"👑 <b>Admin Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"⭐ Premium: <b>{premium_users}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Bugun faol: <b>{today_active}</b>\n"
        f"📊 Haftalik faol: <b>{week_active}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 Jami tranzaksiyalar: <b>{total_txns}</b>\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user_id = query.from_user.id

    # To'lov tugmalari
    if data in ("pay_monthly", "pay_quarterly", "pay_yearly"):
        plans = {
            "pay_monthly":   ("Oylik",   PRICE_MONTHLY,   30),
            "pay_quarterly": ("3 Oylik", PRICE_QUARTERLY, 90),
            "pay_yearly":    ("Yillik",  PRICE_YEARLY,    365),
        }
        plan_name, price, days = plans[data]
        await query.edit_message_text(
            f"💳 <b>{plan_name} — {price:,} so'm</b>\n\n"
            f"Quyidagi rekvizitga to'lov qiling:\n\n"
            f"🏦 <b>Karta:</b> <code>9860 1604 3098 1169</code>\n"
            f"👤 <b>Egasi:</b> Rahmanov Elyorbek\n\n"
            f"To'lov qilgach pastdagi tugmani bosing 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ To'lov qildim", callback_data=f"paid_{data}")
            ]])
        )
        return

    elif data.startswith("paid_"):
        plans = {
            "paid_pay_monthly":   ("Oylik",   PRICE_MONTHLY),
            "paid_pay_quarterly": ("3 Oylik", PRICE_QUARTERLY),
            "paid_pay_yearly":    ("Yillik",  PRICE_YEARLY),
        }
        plan_name, price = plans.get(data, ("Oylik", PRICE_MONTHLY))
        user_name = query.from_user.full_name
        await notify_admin_payment(context, user_id, user_name, plan_name, price)
        await query.edit_message_text(
            "⏳ <b>So'rovingiz yuborildi!</b>\n\n"
            "Admin to'lovni tekshirib, tez orada faollashtiradi.\n"
            "Odatda <b>5-15 daqiqa</b> ichida.",
            parse_mode="HTML"
        )
        return

    elif data.startswith("adm_confirm_"):
        parts = data.split("_")
        target_id = int(parts[2])
        days      = int(parts[3])
        await activate_premium(target_id, days)
        await query.edit_message_text(
            f"✅ Premium faollashtirildi!\n🆔 {target_id} | 📅 {days} kun",
            parse_mode="HTML"
        )
        await context.bot.send_message(
            chat_id=target_id,
            text="🎉 <b>Premium faollashtirildi!</b>\n\n"
                 "Endi botdan to'liq foydalanishingiz mumkin.\n/start",
            parse_mode="HTML"
        )
        return

    elif data.startswith("adm_reject_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_text(f"❌ Bekor qilindi. 🆔 {target_id}")
        await context.bot.send_message(
            chat_id=target_id,
            text="❌ <b>To'lov tasdiqlanmadi.</b>\n\n"
                 "Muammo bo'lsa admin bilan bog'laning.",
            parse_mode="HTML"
        )
        return

    # Premium tekshiruv
    premium = await is_user_premium(user_id)
    if not premium:
        await show_payment_screen(update, context)
        return

    if data == "add_income":
        context.user_data["txn_type"] = "income"
        await query.edit_message_text(
            "📥 <b>Daromad kategoriyasini tanlang:</b>",
            parse_mode="HTML",
            reply_markup=category_keyboard(INCOME_CATEGORIES, "income"))

    elif data == "add_expense":
        context.user_data["txn_type"] = "expense"
        await query.edit_message_text(
            "📤 <b>Xarajat kategoriyasini tanlang:</b>",
            parse_mode="HTML",
            reply_markup=category_keyboard(EXPENSE_CATEGORIES, "expense"))

    elif data.startswith("cat_"):
        _, txn_type, idx = data.split("_", 2)
        cats     = INCOME_CATEGORIES if txn_type == "income" else EXPENSE_CATEGORIES
        category = cats[int(idx)]
        context.user_data.update({
            "category": category,
            "txn_type": txn_type,
        })
        # Kategoriya tanlangach balans tanlash chiqadi
        bals = await get_balances(user_id)
        emoji = "📥" if txn_type == "income" else "📤"
        action = "qaysi balansga tushadi" if txn_type == "income" else "qaysi balansdan chiqadi"
        await query.edit_message_text(
            f"{emoji} <b>Kategoriya:</b> {category}\n\n"
            f"💳 Pul {action}?",
            parse_mode="HTML",
            reply_markup=balance_select_keyboard(bals))

    elif data.startswith("selbal_"):
        balance_id = int(data.split("_")[1])
        context.user_data["balance_id"] = balance_id
        context.user_data["awaiting_amount"] = True

        # Voice rejimidan kelgan bo'lsa — to'g'ri saqlash
        if context.user_data.get("voice_parsed"):
            parsed = context.user_data["voice_parsed"]
            await add_transaction(
                user_id, parsed["type"], parsed["amount"],
                parsed["category"], parsed.get("text", ""), balance_id
            )
            context.user_data.pop("voice_parsed", None)
            context.user_data.pop("balance_id", None)
            context.user_data.pop("awaiting_amount", None)

            txns   = await get_month_transactions(user_id)
            stats  = calc_stats(txns)
            emoji  = "📥" if parsed["type"] == "income" else "📤"
            type_t = "Daromad" if parsed["type"] == "income" else "Xarajat"

            await query.edit_message_text(
                f"✅ <b>{type_t} saqlandi!</b>\n\n"
                f"{emoji} {format_money(parsed['amount'])}\n"
                f"📁 {parsed['category']}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 {format_money(stats['income'])}  "
                f"📤 {format_money(stats['expenses'])}  "
                f"💵 {format_money(stats['balance'])}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")
                ]])
            )
            return

        # Oddiy rejim — miqdor so'rash
        emoji = "📥" if context.user_data.get("txn_type") == "income" else "📤"
        await query.edit_message_text(
            f"{emoji} <b>Kategoriya:</b> {context.user_data.get('category')}\n\n"
            f"💬 Miqdorni kiriting (faqat raqam):\n<i>Masalan: 50000</i>",
            parse_mode="HTML")

    elif data == "stats":
        txns   = await get_month_transactions(user_id)
        stats  = calc_stats(txns)
        budget = await get_budget(user_id)
        cat_stats = {}
        for t in txns:
            if t["type"] == "expense":
                cat = t.get("category", "Boshqa")
                cat_stats[cat] = cat_stats.get(cat, 0) + float(t["amount"])

        msg = f"📊 <b>Statistika — {datetime.now().strftime('%B %Y')}</b>\n\n"
        msg += "┌─────────────────────────┐\n"
        msg += f"│ 📥 Daromad : {format_money(stats['income']):>12} │\n"
        msg += f"│ 📤 Xarajat : {format_money(stats['expenses']):>12} │\n"
        msg += f"│ 💵 Balans  : {format_money(stats['balance']):>12} │\n"
        msg += "└─────────────────────────┘\n"

        if budget > 0:
            used = int(stats['expenses'] / budget * 100) if budget else 0
            rem  = budget - stats['expenses']
            pct  = min(int(stats["expenses"] / budget * 10), 10)
            bar  = "🟥" * pct + "⬜" * (10 - pct)
            msg += f"\n🎯 <b>Budget:</b>\n"
            msg += f"  {bar} {used}%\n"
            msg += f"  Belgilangan : {format_money(budget)}\n"
            msg += f"  Sarflangan  : {format_money(stats['expenses'])}\n"
            msg += f"  Qolgan      : {format_money(max(rem, 0))}\n"
            if rem < 0:
                msg += f"  ⚠️ {format_money(abs(rem))} oshib ketdi!\n"

        if cat_stats:
            msg += f"\n🏆 <b>Top xarajatlar:</b>\n"
            msg += "─" * 30 + "\n"
            for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1])[:5]:
                pct = int(amt / stats['expenses'] * 100) if stats['expenses'] else 0
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                msg += f"{cat}\n  {bar} {pct}%  {format_money(amt)}\n"

        await query.edit_message_text(msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📄 PDF yuklab olish", callback_data="stats_pdf")],
                [InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")]
            ]))

    elif data == "stats_pdf":
        txns   = await get_month_transactions(user_id)
        stats  = calc_stats(txns)
        budget = await get_budget(user_id)
        cat_stats = {}
        for t in txns:
            if t["type"] == "expense":
                cat = t.get("category", "Boshqa")
                cat_stats[cat] = cat_stats.get(cat, 0) + float(t["amount"])

        user_name = query.from_user.full_name
        month_str = datetime.now().strftime("%B %Y")

        await query.answer("PDF tayyorlanmoqda...")

        pdf_bytes = generate_stats_pdf(user_name, stats, cat_stats, budget, month_str)

        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(pdf_bytes),
            filename=f"hisobot_{datetime.now().strftime('%Y_%m')}.pdf",
            caption=f"📄 <b>{month_str} hisoboti</b>",
            parse_mode="HTML"
        )

    elif data == "history":
        months = await get_available_months(user_id)
        if not months:
            await query.edit_message_text(
                "📋 <b>Hali tranzaksiyalar yo'q.</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")]]))
            return
        await query.edit_message_text(
            "📋 <b>Qaysi oyni ko'rmoqchisiz?</b>",
            parse_mode="HTML",
            reply_markup=history_months_keyboard(months))

    elif data.startswith("history_"):
        _, year, month = data.split("_")
        year, month = int(year), int(month)
        txns = await get_transactions_by_month(user_id, year, month)
        if not txns:
            msg = f"📋 <b>{MONTH_NAMES[month]} {year} — tranzaksiyalar yo'q.</b>"
        else:
            income   = sum(float(t['amount']) for t in txns if t['type'] == 'income')
            expenses = sum(float(t['amount']) for t in txns if t['type'] == 'expense')
            msg = (
                f"📋 <b>{MONTH_NAMES[month]} {year}</b>\n"
                f"📥 Daromad: <b>{format_money(income)}</b>\n"
                f"📤 Xarajat: <b>{format_money(expenses)}</b>\n"
                f"💵 Balans: <b>{format_money(income - expenses)}</b>\n\n"
            )
            for t in txns:
                emoji = "📥" if t["type"] == "income" else "📤"
                date  = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else str(t["date"])[:10]
                bal   = f" | 💳 {t['balance_name']}" if t.get("balance_name") else ""
                note  = f" — {t['note']}" if t.get("note") else ""
                msg  += f"{emoji} <b>{format_money(float(t['amount']))}</b>\n   📁 {t.get('category','Boshqa')}{bal} | 📅 {date}{note}\n\n"

        await query.edit_message_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Oylar", callback_data="history"),
                InlineKeyboardButton("🏠 Menyu", callback_data="back_main")]]))

    elif data == "debts":
        debts = await get_debts(user_id)
        gave  = [d for d in debts if d["direction"] == "gave"]
        took  = [d for d in debts if d["direction"] == "took"]

        msg = "💸 <b>Qarzlar ro'yxati</b>\n\n"
        if gave:
            total_gave = sum(float(d["amount"]) for d in gave)
            msg += f"🔴 <b>Men berganlar</b>:\n"
            msg += f"Jami: <b>{format_money(total_gave)}</b>\n\n"
            for d in gave:
                due = f" | 📅 {d['due_date'].strftime('%d.%m.%Y')}" if d["due_date"] else ""
                msg += f"👤 {d['person_name']} — <b>{format_money(float(d['amount']))}</b>{due}\n"
            msg += "\n"
        if took:
            total_took = sum(float(d["amount"]) for d in took)
            msg += f"🟢 <b>Men olganlar</b>:\n"
            msg += f"Jami: <b>{format_money(total_took)}</b>\n\n"
            for d in took:
                due = f" | 📅 {d['due_date'].strftime('%d.%m.%Y')}" if d["due_date"] else ""
                msg += f"👤 {d['person_name']} — <b>{format_money(float(d['amount']))}</b>{due}\n"
        if not debts:
            msg += "✅ Hozircha qarz yo'q!"

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Yangi qarz", callback_data="add_debt")],
            [InlineKeyboardButton("✅ Qarz to'landi", callback_data="debt_paid_list")],
            [InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")],
        ])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)

    elif data == "add_debt":
        await query.edit_message_text(
            "💸 <b>Yangi qarz</b>\n\nQarz yo'nalishini tanlang:",
            parse_mode="HTML",
            reply_markup=debt_direction_keyboard()
        )

    elif data in ("debt_dir_gave", "debt_dir_took"):
        context.user_data["debt_direction"] = "gave" if data == "debt_dir_gave" else "took"
        context.user_data["awaiting_debt_person"] = True
        direction_text = "bergan" if data == "debt_dir_gave" else "olgan"
        await query.edit_message_text(
            f"👤 Qarz {direction_text} odamning <b>ismini</b> yozing:\n"
            f"<i>Masalan: Akbar</i>",
            parse_mode="HTML"
        )

    elif data == "debt_paid_list":
        debts = await get_debts(user_id)
        if not debts:
            await query.edit_message_text(
                "✅ <b>Hozircha to'lanmagan qarz yo'q!</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Orqaga", callback_data="debts")]]))
            return
        buttons = []
        for d in debts:
            direction = "🔴" if d["direction"] == "gave" else "🟢"
            label = f"{direction} {d['person_name']} — {format_money(float(d['amount']))}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"mark_paid_{d['id']}")])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="debts")])
        await query.edit_message_text(
            "✅ <b>Qaysi qarz to'landi?</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("mark_paid_"):
        debt_id = int(data.split("_")[2])
        await mark_debt_paid(debt_id)
        await query.edit_message_text(
            "✅ <b>Qarz to'landi deb belgilandi!</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Qarzlar", callback_data="debts")]]))

    elif data == "balances":
        bals = await get_balances(user_id)
        msg = "💳 <b>Balanslar</b>\n\n"
        if bals:
            total = sum(float(b["amount"]) for b in bals)
            for b in bals:
                type_name = BALANCE_TYPES.get(b["type"], "📦 Boshqa")
                msg += f"{type_name} — <b>{b['name']}</b>\n"
                msg += f"   💵 {format_money(float(b['amount']))}\n\n"
            msg += f"━━━━━━━━━━━━━━━━━━━━\n"
            msg += f"💰 Jami: <b>{format_money(total)}</b>"
        else:
            msg += "Hali balans qo'shilmagan."

        buttons = []
        if bals:
            bal_buttons = []
            for b in bals:
                bal_buttons.append(
                    InlineKeyboardButton(f"✏️ {b['name']}", callback_data=f"bal_edit_{b['id']}")
                )
                if len(bal_buttons) == 2:
                    buttons.append(bal_buttons)
                    bal_buttons = []
            if bal_buttons:
                buttons.append(bal_buttons)

        buttons.append([InlineKeyboardButton("➕ Yangi balans", callback_data="add_balance")])
        buttons.append([InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")])
        await query.edit_message_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "add_balance":
        await query.edit_message_text(
            "💳 <b>Balans turi</b>\n\nQaysi turdagi balans qo'shmoqchisiz?",
            parse_mode="HTML",
            reply_markup=balance_type_keyboard()
        )

    elif data.startswith("bal_type_"):
        bal_type = data.replace("bal_type_", "")
        context.user_data["balance_type"] = bal_type
        context.user_data["awaiting_balance_name"] = True
        type_name = BALANCE_TYPES.get(bal_type, "Boshqa")
        await query.edit_message_text(
            f"{type_name} uchun <b>nom</b> kiriting:\n"
            f"<i>Masalan: Kapitalbank, Naqd, Hamyon</i>",
            parse_mode="HTML"
        )

    elif data.startswith("bal_edit_"):
        bal_id = int(data.split("_")[2])
        context.user_data["editing_balance_id"] = bal_id
        context.user_data["awaiting_balance_update"] = True
        await query.edit_message_text(
            "💰 Yangi miqdorni kiriting:\n<i>Masalan: 500000</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ O'chirish", callback_data=f"bal_delete_{bal_id}"),
                InlineKeyboardButton("🔙 Orqaga", callback_data="balances")
            ]])
        )

    elif data.startswith("bal_delete_"):
        bal_id = int(data.split("_")[2])
        await delete_balance(bal_id)
        await query.edit_message_text(
            "🗑️ <b>Balans o'chirildi!</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Balanslar", callback_data="balances")]]))

    elif data == "set_budget":
        context.user_data["awaiting_budget"] = True
        await query.edit_message_text(
            "🎯 <b>Oylik budget miqdorini kiriting:</b>\n\n<i>Masalan: 2000000</i>",
            parse_mode="HTML")

    elif data == "clear_month":
        await query.edit_message_text(
            "⚠️ <b>Diqqat!</b>\n\nBu oyning barcha ma'lumotlarini o'chirishni istaysizmi?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Ha", callback_data="confirm_clear"),
                InlineKeyboardButton("❌ Yo'q", callback_data="back_main")]]))

    elif data == "confirm_clear":
        await clear_month_transactions(user_id)
        await query.edit_message_text("🗑️ Bu oyning ma'lumotlari o'chirildi.\n\n/start")

    elif data == "skip_note":
        await _save_transaction(user_id, context, note="", via_query=query)

    elif data == "debt_skip_date":
        await _save_debt(user_id, context, due_date=None, via_query=query)

    elif data == "admin_panel":
        if user_id != ADMIN_ID:
            await query.edit_message_text("❌ Ruxsat yo'q.")
            return
        async with db_pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            premium_users = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE is_premium = TRUE AND premium_until > NOW()"
            )
            today_active = await conn.fetchval("""
                SELECT COUNT(DISTINCT telegram_id) FROM transactions
                WHERE DATE(date AT TIME ZONE 'Asia/Tashkent') = CURRENT_DATE
            """)
            week_active = await conn.fetchval("""
                SELECT COUNT(DISTINCT telegram_id) FROM transactions
                WHERE date >= NOW() - INTERVAL '7 days'
            """)
            total_txns = await conn.fetchval("SELECT COUNT(*) FROM transactions")

        msg = (
            f"👑 <b>Admin Panel</b>\n\n"
            f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
            f"⭐ Premium: <b>{premium_users}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Bugun faol: <b>{today_active}</b>\n"
            f"📊 Haftalik faol: <b>{week_active}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 Jami tranzaksiyalar: <b>{total_txns}</b>\n"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔔 Eslatma yuborish (test)", callback_data="admin_send_reminder")],
            [InlineKeyboardButton("📊 Haftalik hisobot (test)", callback_data="admin_send_weekly")],
            [InlineKeyboardButton("📢 Broadcast xabar", callback_data="admin_broadcast")],
            [InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")],
        ])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)

    elif data == "admin_send_reminder":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "🔔 <b>Eslatmalar yuborilmoqda...</b>\n\n"
            "Bugun tranzaksiya kiritmagan foydalanuvchilarga eslatma boradi.",
            parse_mode="HTML"
        )
        await send_daily_reminders(context.bot)
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ <b>Eslatmalar yuborildi!</b>\n\nNatijani Render logs'dan ko'ring.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")
            ]])
        )

    elif data == "admin_send_weekly":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "📊 <b>Haftalik hisobotlar yuborilmoqda...</b>\n\n"
            "Barcha faol foydalanuvchilarga PDF hisobot boradi.\n"
            "Biroz vaqt oladi (PDF yaratish sekin).",
            parse_mode="HTML"
        )
        await send_weekly_reports(context.bot)
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ <b>Haftalik hisobotlar yuborildi!</b>\n\nNatijani Render logs'dan ko'ring.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")
            ]])
        )

    elif data == "admin_broadcast":
        if user_id != ADMIN_ID:
            return
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            "📢 <b>Broadcast xabar</b>\n\n"
            "Barcha foydalanuvchilarga yubormoqchi bo'lgan xabarni yozing:\n\n"
            "<i>HTML teglar qo'llab-quvvatlanadi (&lt;b&gt;, &lt;i&gt;, &lt;code&gt;)</i>\n\n"
            "Bekor qilish uchun /start bosing.",
            parse_mode="HTML"
        )

    elif data == "back_main":
        txns  = await get_month_transactions(user_id)
        stats = calc_stats(txns)
        await query.edit_message_text(
            f"🏠 <b>Bosh menyu</b>\n\n"
            f"📅 {datetime.now().strftime('%B %Y')}\n"
            f"📥 {format_money(stats['income'])}\n"
            f"📤 {format_money(stats['expenses'])}\n"
            f"💵 {format_money(stats['balance'])}",
            parse_mode="HTML", reply_markup=main_keyboard(user_id))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()

    if not any([
        context.user_data.get("awaiting_amount"),
        context.user_data.get("awaiting_note"),
        context.user_data.get("awaiting_budget"),
        context.user_data.get("awaiting_debt_person"),
        context.user_data.get("awaiting_debt_amount"),
        context.user_data.get("awaiting_debt_date"),
        context.user_data.get("awaiting_balance_name"),
        context.user_data.get("awaiting_balance_amount"),
        context.user_data.get("awaiting_balance_update"),
        context.user_data.get("awaiting_broadcast"),
    ]):
        premium = await is_user_premium(user_id)
        if not premium:
            await show_payment_screen(update, context)
            return
        await update.message.reply_text("👇 Boshlash uchun /start yuboring.")
        return

    # Broadcast xabar (faqat admin)
    if context.user_data.get("awaiting_broadcast"):
        context.user_data.pop("awaiting_broadcast", None)
        if user_id != ADMIN_ID:
            return
        await update.message.reply_text(
            f"📢 <b>Yuborilmoqda...</b>\n\nXabar matni:\n\n{text}",
            parse_mode="HTML"
        )
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT telegram_id FROM users")
        sent = 0
        failed = 0
        for row in rows:
            try:
                await context.bot.send_message(
                    chat_id=row["telegram_id"],
                    text=text,
                    parse_mode="HTML"
                )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ Broadcast xato {row['telegram_id']}: {e}")
            await asyncio.sleep(0.1)
        await update.message.reply_text(
            f"✅ <b>Broadcast tugadi!</b>\n\n"
            f"📤 Yuborildi: <b>{sent}</b>\n"
            f"❌ Xato: <b>{failed}</b>",
            parse_mode="HTML"
        )
        return

    if context.user_data.get("awaiting_balance_name"):
        context.user_data["balance_name"] = text
        context.user_data.pop("awaiting_balance_name")
        context.user_data["awaiting_balance_amount"] = True
        await update.message.reply_text(
            f"💳 Nom: <b>{text}</b>\n\n"
            f"💰 Hozirgi miqdorini kiriting:\n<i>Masalan: 500000</i>",
            parse_mode="HTML"
        )

    elif context.user_data.get("awaiting_balance_amount"):
        try:
            amount = float(text.replace(" ", "").replace(",", ""))
            if amount < 0:
                raise ValueError
            name     = context.user_data.get("balance_name", "")
            bal_type = context.user_data.get("balance_type", "other")
            await add_balance(user_id, name, bal_type, amount)
            for k in ("balance_name", "balance_type", "awaiting_balance_amount"):
                context.user_data.pop(k, None)
            type_name = BALANCE_TYPES.get(bal_type, "📦 Boshqa")
            await update.message.reply_text(
                f"✅ <b>Balans qo'shildi!</b>\n\n"
                f"{type_name} — <b>{name}</b>\n"
                f"💵 {format_money(amount)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💳 Balanslar", callback_data="balances"),
                    InlineKeyboardButton("🏠 Menyu", callback_data="back_main")
                ]])
            )
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

    elif context.user_data.get("awaiting_balance_update"):
        try:
            amount = float(text.replace(" ", "").replace(",", ""))
            if amount < 0:
                raise ValueError
            bal_id = context.user_data.get("editing_balance_id")
            await update_balance(bal_id, amount)
            context.user_data.pop("awaiting_balance_update", None)
            context.user_data.pop("editing_balance_id", None)
            await update.message.reply_text(
                f"✅ <b>Balans yangilandi!</b>\n💵 {format_money(amount)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💳 Balanslar", callback_data="balances"),
                    InlineKeyboardButton("🏠 Menyu", callback_data="back_main")
                ]])
            )
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

    elif context.user_data.get("awaiting_debt_person"):
        context.user_data["debt_person"] = text
        context.user_data.pop("awaiting_debt_person")
        context.user_data["awaiting_debt_amount"] = True
        await update.message.reply_text(
            f"👤 Ism: <b>{text}</b>\n\n"
            f"💰 Qarz miqdorini kiriting:\n<i>Masalan: 100000</i>",
            parse_mode="HTML"
        )

    elif context.user_data.get("awaiting_debt_amount"):
        try:
            amount = float(text.replace(" ", "").replace(",", ""))
            if amount <= 0:
                raise ValueError
            context.user_data["debt_amount"] = amount
            context.user_data.pop("awaiting_debt_amount")
            context.user_data["awaiting_debt_date"] = True
            await update.message.reply_text(
                f"💰 Miqdor: <b>{format_money(amount)}</b>\n\n"
                f"📅 Qaytarish sanasini kiriting:\n"
                f"<i>Masalan: 15.05.2026</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️ Sana yo'q", callback_data="debt_skip_date")
                ]])
            )
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

    elif context.user_data.get("awaiting_debt_date"):
        try:
            due_date = datetime.strptime(text, "%d.%m.%Y").date()
            await _save_debt(user_id, context, due_date=due_date,
                             reply_fn=update.message.reply_text)
        except ValueError:
            await update.message.reply_text(
                "❌ Sana formati noto'g'ri.\n<i>Masalan: 15.05.2026</i>",
                parse_mode="HTML"
            )

    elif context.user_data.get("awaiting_amount"):
        try:
            amount = float(text.replace(" ", "").replace(",", ""))
            if amount <= 0:
                raise ValueError
            context.user_data.update({
                "amount": amount,
                "awaiting_amount": False,
                "awaiting_note": True
            })
            await update.message.reply_text(
                f"✅ Miqdor: <b>{format_money(amount)}</b>\n\n"
                f"📝 Izoh qo'shmoqchimisiz? (Ixtiyoriy)",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏭️ O'tkazib yuborish", callback_data="skip_note")]]))
        except ValueError:
            await update.message.reply_text(
                "❌ Faqat musbat raqam kiriting. <i>Masalan: 50000</i>",
                parse_mode="HTML")

    elif context.user_data.get("awaiting_note"):
        await _save_transaction(user_id, context, note=text,
                                reply_fn=update.message.reply_text)

    elif context.user_data.get("awaiting_budget"):
        try:
            budget = float(text.replace(" ", "").replace(",", ""))
            if budget <= 0:
                raise ValueError
            await set_budget(user_id, budget)
            context.user_data.pop("awaiting_budget")
            await update.message.reply_text(
                f"✅ <b>Oylik budget belgilandi!</b>\n\n"
                f"🎯 Budget: <b>{format_money(budget)}</b>\n\n/start",
                parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

async def _save_debt(user_id, context, due_date=None, reply_fn=None, via_query=None):
    person    = context.user_data.get("debt_person", "")
    amount    = context.user_data.get("debt_amount", 0)
    direction = context.user_data.get("debt_direction", "gave")

    for k in ("debt_person", "debt_amount", "debt_direction", "awaiting_debt_date"):
        context.user_data.pop(k, None)

    await add_debt(user_id, person, amount, direction, due_date)

    direction_text = "bergan" if direction == "gave" else "olgan"
    due_text = f"\n📅 Qaytarish: {due_date.strftime('%d.%m.%Y')}" if due_date else ""
    emoji = "🔴" if direction == "gave" else "🟢"

    msg = (
        f"✅ <b>Qarz saqlandi!</b>\n\n"
        f"{emoji} Men <b>{direction_text}</b>\n"
        f"👤 Ism: <b>{person}</b>\n"
        f"💰 Miqdor: <b>{format_money(amount)}</b>{due_text}"
    )
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("💸 Qarzlar", callback_data="debts"),
        InlineKeyboardButton("🏠 Menyu", callback_data="back_main")
    ]])

    if via_query:
        await via_query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)
    elif reply_fn:
        await reply_fn(msg, parse_mode="HTML", reply_markup=markup)

async def _save_transaction(user_id, context, note="",
                            reply_fn=None, via_query=None):
    amount     = context.user_data.get("amount")
    category   = context.user_data.get("category", "📦 Boshqa")
    txn_type   = context.user_data.get("txn_type", "expense")
    balance_id = context.user_data.get("balance_id")

    for k in ("amount", "category", "txn_type", "balance_id",
              "awaiting_amount", "awaiting_note"):
        context.user_data.pop(k, None)

    if not amount:
        return

    await add_transaction(user_id, txn_type, amount, category, note, balance_id)
    txns   = await get_month_transactions(user_id)
    stats  = calc_stats(txns)
    budget = await get_budget(user_id)

    emoji  = "📥" if txn_type == "income" else "📤"
    note_t = f"\n📝 Izoh: {note}" if note else ""

    # Balans nomini olish
    bal_text = ""
    if balance_id:
        async with db_pool.acquire() as conn:
            bal = await conn.fetchrow(
                "SELECT name, amount FROM balances WHERE id = $1", balance_id
            )
            if bal:
                bal_text = f"\n💳 Balans: <b>{bal['name']}</b> — {format_money(float(bal['amount']))}"

    msg = (
        f"✅ <b>{'Daromad' if txn_type=='income' else 'Xarajat'} saqlandi!</b>\n\n"
        f"{emoji} Miqdor    : <b>{format_money(amount)}</b>\n"
        f"📁 Kategoriya: {category}{note_t}{bal_text}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 {format_money(stats['income'])}  "
        f"📤 {format_money(stats['expenses'])}  "
        f"💵 {format_money(stats['balance'])}\n"
    )
    if budget > 0 and txn_type == "expense":
        rem = budget - stats["expenses"]
        if rem < 0:
            msg += f"\n⚠️ <b>Budget {format_money(abs(rem))} oshib ketdi!</b>"
        elif rem < budget * 0.2:
            msg += f"\n⚠️ Budget tugayapti! Qolgan: {format_money(rem)}"

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")]])

    if via_query:
        await via_query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)
    elif reply_fn:
        await reply_fn(msg, parse_mode="HTML", reply_markup=markup)

# ===================== KUNLIK ESLATMA =====================

def generate_weekly_pdf(user_name, week_data, week_start, week_end):
    """Haftalik hisobot uchun chiroyli PDF yaratadi."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                           rightMargin=2*cm, leftMargin=2*cm,
                           topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'Title', parent=styles['Title'],
        fontSize=18, spaceAfter=10, textColor=colors.HexColor('#2255A8')
    )
    subtitle_style = ParagraphStyle(
        'Subtitle', parent=styles['Normal'],
        fontSize=13, spaceAfter=15, textColor=colors.HexColor('#666666')
    )
    normal_style = ParagraphStyle(
        'Normal', parent=styles['Normal'],
        fontSize=11, spaceAfter=6
    )
    heading_style = ParagraphStyle(
        'Heading', parent=styles['Heading2'],
        fontSize=13, spaceAfter=10, textColor=colors.HexColor('#2255A8')
    )

    elements = []

    # Sarlavha
    elements.append(Paragraph("Haftalik Moliyaviy Hisobot", title_style))
    elements.append(Paragraph(
        f"Foydalanuvchi: {user_name} | "
        f"Davr: {week_start.strftime('%d.%m.%Y')} - {week_end.strftime('%d.%m.%Y')}",
        subtitle_style
    ))
    elements.append(Spacer(1, 0.3*cm))

    # Umumiy ma'lumot
    income = week_data['income']
    expense = week_data['expense']
    balance = income - expense
    balance_status = "+" if balance >= 0 else ""

    summary_data = [
        ["Ko'rsatkich", "Miqdor"],
        ["Jami daromad", f"{income:,.0f} so'm"],
        ["Jami xarajat", f"{expense:,.0f} so'm"],
        ["Sof natija", f"{balance_status}{balance:,.0f} so'm"],
    ]
    if week_data['tx_count'] > 0:
        avg_daily = expense / 7
        summary_data.append(["Kunlik o'rtacha xarajat", f"{avg_daily:,.0f} so'm"])
        summary_data.append(["Tranzaksiyalar soni", f"{week_data['tx_count']} ta"])

    summary_table = Table(summary_data, colWidths=[10*cm, 7*cm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2255A8')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('FONTSIZE', (0, 1), (-1, -1), 11),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4FF')]),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 0.7*cm))

    # Kategoriyalar bo'yicha
    if week_data['categories']:
        elements.append(Paragraph("Kategoriyalar bo'yicha xarajatlar:", heading_style))
        cat_data = [["Kategoriya", "Miqdor", "Foiz"]]
        total_exp = expense if expense > 0 else 1
        for cat, amt in sorted(week_data['categories'].items(), key=lambda x: -x[1]):
            pct = int(amt / total_exp * 100)
            cat_data.append([cat, f"{amt:,.0f} so'm", f"{pct}%"])
        cat_table = Table(cat_data, colWidths=[9*cm, 6*cm, 2*cm])
        cat_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2255A8')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 0), (2, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4FF')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(cat_table)
        elements.append(Spacer(1, 0.7*cm))

    # Kunlik xarajatlar
    if week_data['daily']:
        elements.append(Paragraph("Kunlik xarajatlar:", heading_style))
        daily_data = [["Kun", "Xarajat", "Tranzaksiya"]]
        day_names_uz = {
            0: "Dushanba", 1: "Seshanba", 2: "Chorshanba",
            3: "Payshanba", 4: "Juma", 5: "Shanba", 6: "Yakshanba"
        }
        for day_date, day_info in sorted(week_data['daily'].items()):
            day_name = day_names_uz.get(day_date.weekday(), "")
            date_str = f"{day_name}, {day_date.strftime('%d.%m')}"
            daily_data.append([
                date_str,
                f"{day_info['amount']:,.0f} so'm",
                f"{day_info['count']} ta"
            ])
        daily_table = Table(daily_data, colWidths=[7*cm, 6*cm, 4*cm])
        daily_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2255A8')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (1, 0), (2, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F4FF')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(daily_table)

    # Pastki qismda izoh
    elements.append(Spacer(1, 1*cm))
    footer_style = ParagraphStyle(
        'Footer', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor('#888888'), alignment=1
    )
    elements.append(Paragraph(
        "Oson Byudjet — Shaxsiy moliya yordamchingiz | @monthbudget_bot",
        footer_style
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()


async def send_weekly_reports(bot):
    """Har Dushanba ertalab 9:00 da o'tgan haftaning hisobotini yuboradi."""
    try:
        async with db_pool.acquire() as conn:
            # Barcha foydalanuvchilarni olish
            users = await conn.fetch("SELECT telegram_id, name FROM users")

        logger.info(f"📊 Haftalik hisobot: {len(users)} foydalanuvchiga yuboriladi")

        # O'tgan hafta sanalari (Dushanbadan Yakshanbagacha)
        today = datetime.now(pytz.timezone("Asia/Tashkent")).date()
        # Bugun Dushanba, o'tgan hafta — 7 kun oldin Dushanbadan 1 kun oldin Yakshanbagacha
        last_monday = today - timedelta(days=7)
        last_sunday = today - timedelta(days=1)

        sent = 0
        failed = 0
        skipped = 0

        for user in users:
            user_id = user["telegram_id"]
            name = user["name"] or "Do'stim"

            # Premium/sinov tekshiruvi
            premium = await is_user_premium(user_id)
            if not premium:
                skipped += 1
                continue

            # O'tgan hafta ma'lumotlari
            async with db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT type, amount, category, date
                    FROM transactions
                    WHERE telegram_id = $1
                      AND DATE(date AT TIME ZONE 'Asia/Tashkent') >= $2
                      AND DATE(date AT TIME ZONE 'Asia/Tashkent') <= $3
                """, user_id, last_monday, last_sunday)

            # Agar o'tgan hafta tranzaksiya bo'lmasa — motivatsion xabar yuboramiz
            if not rows:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"📊 <b>Haftalik hisobot</b>\n\n"
                            f"Assalomu alaykum, {name}!\n\n"
                            f"📅 {last_monday.strftime('%d.%m')} - {last_sunday.strftime('%d.%m.%Y')}\n\n"
                            f"O'tgan haftada hech qanday xarajat kiritmadingiz 📭\n\n"
                            f"Moliyaviy nazorat — boy bo'lishning birinchi qadami!\n"
                            f"Bu haftadan boshlab xarajatlaringizni yozib boring 💪"
                        ),
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("➕ Xarajat qo'shish", callback_data="add_expense")
                        ]])
                    )
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.warning(f"⚠️ Haftalik xabar yuborilmadi {user_id}: {e}")
                await asyncio.sleep(0.1)
                continue

            # Ma'lumotlarni guruhlash
            income = 0.0
            expense = 0.0
            categories = {}
            daily = {}

            for r in rows:
                amt = float(r["amount"])
                tx_date = r["date"].astimezone(pytz.timezone("Asia/Tashkent")).date()

                if r["type"] == "income":
                    income += amt
                else:
                    expense += amt
                    # Kategoriya
                    cat = r["category"] or "Boshqa"
                    categories[cat] = categories.get(cat, 0) + amt
                    # Kunlik
                    if tx_date not in daily:
                        daily[tx_date] = {"amount": 0, "count": 0}
                    daily[tx_date]["amount"] += amt
                    daily[tx_date]["count"] += 1

            week_data = {
                "income": income,
                "expense": expense,
                "tx_count": len(rows),
                "categories": categories,
                "daily": daily,
            }

            # PDF yaratish
            try:
                pdf_bytes = generate_weekly_pdf(
                    name, week_data,
                    last_monday, last_sunday
                )

                balance = income - expense
                balance_emoji = "✅" if balance >= 0 else "⚠️"
                top_cat = max(categories.items(), key=lambda x: x[1])[0] if categories else "—"

                caption = (
                    f"📊 <b>Haftalik hisobot</b>\n\n"
                    f"📅 {last_monday.strftime('%d.%m')} - {last_sunday.strftime('%d.%m.%Y')}\n\n"
                    f"📥 Daromad: <b>{format_money(income)}</b>\n"
                    f"📤 Xarajat: <b>{format_money(expense)}</b>\n"
                    f"{balance_emoji} Natija: <b>{format_money(balance)}</b>\n\n"
                    f"🏆 Eng ko'p: {top_cat}\n\n"
                    f"📄 To'liq tahlil PDF faylda ⬆️"
                )

                await bot.send_document(
                    chat_id=user_id,
                    document=io.BytesIO(pdf_bytes),
                    filename=f"haftalik_hisobot_{last_monday.strftime('%Y_%m_%d')}.pdf",
                    caption=caption,
                    parse_mode="HTML"
                )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ PDF xato {user_id}: {e}")

            await asyncio.sleep(0.15)  # Rate limit

        logger.info(
            f"✅ Haftalik hisobot: {sent} yuborildi | "
            f"❌ {failed} xato | ⏭️ {skipped} o'tkazildi"
        )

    except Exception as e:
        logger.error(f"❌ Haftalik hisobot xato: {e}")


# ===================== KUNLIK ESLATMA =====================

async def send_daily_reminders(bot):
    """Har kuni 20:00 (Toshkent) da bugun xarajat kiritmaganlarga eslatma yuboradi."""
    try:
        async with db_pool.acquire() as conn:
            # Bugun tranzaksiya kiritmagan foydalanuvchilarni topish
            rows = await conn.fetch("""
                SELECT u.telegram_id, u.name
                FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM transactions t
                    WHERE t.telegram_id = u.telegram_id
                      AND DATE(t.date AT TIME ZONE 'Asia/Tashkent') = CURRENT_DATE
                )
            """)

        logger.info(f"📬 Eslatma yuboriladi: {len(rows)} foydalanuvchi")

        sent = 0
        failed = 0
        for row in rows:
            user_id = row["telegram_id"]
            name = row["name"] or "Do'stim"

            # Premium yoki sinov muddati tekshirish
            premium = await is_user_premium(user_id)
            if not premium:
                continue  # Muddati tugaganlarga yubormaymiz

            # Haftalik statistika (shu hafta)
            async with db_pool.acquire() as conn:
                week_row = await conn.fetchrow("""
                    SELECT
                        COALESCE(SUM(CASE WHEN type='income' THEN amount END), 0) AS income,
                        COALESCE(SUM(CASE WHEN type='expense' THEN amount END), 0) AS expense
                    FROM transactions
                    WHERE telegram_id = $1
                      AND date >= DATE_TRUNC('week', NOW())
                """, user_id)

            income = float(week_row["income"])
            expense = float(week_row["expense"])
            balance = income - expense

            msg = (
                f"🌙 <b>Assalomu alaykum, {name}!</b>\n\n"
                f"Bugun hali xarajat yoki daromad kiritmadingiz 📝\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Bu hafta:</b>\n"
                f"📥 Daromad: <b>{format_money(income)}</b>\n"
                f"📤 Xarajat: <b>{format_money(expense)}</b>\n"
                f"💵 Balans: <b>{format_money(balance)}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Hoziroq qo'shishni unutmang! 👇"
            )

            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
                 InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
                [InlineKeyboardButton("📊 Statistika", callback_data="stats")],
            ])

            try:
                await bot.send_message(
                    chat_id=user_id, text=msg,
                    parse_mode="HTML", reply_markup=markup
                )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ Eslatma yuborilmadi {user_id}: {e}")

            # Telegram rate limit: sekundiga 30 xabar chegarasidan pastda qolamiz
            await asyncio.sleep(0.1)

        logger.info(f"✅ Eslatmalar yuborildi: {sent} ta | ❌ Xato: {failed} ta")

    except Exception as e:
        logger.error(f"❌ Eslatma funksiyasida xato: {e}")

# ===================== WEBHOOK =====================

async def health(request):
    return web.Response(text="✅ Oson Byudjet Bot is alive!", status=200)

async def webhook_handler(request, application):
    data   = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(status=200)

async def main():
    await init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("testreminder", admin_test_reminder))
    app.add_handler(CommandHandler("adminstats", admin_stats))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    await app.initialize()
    await app.start()

    webhook_path = f"/webhook/{BOT_TOKEN}"
    await app.bot.set_webhook(url=f"{WEBHOOK_URL}{webhook_path}")
    logger.info(f"✅ Webhook set: {WEBHOOK_URL}{webhook_path}")

    # Kunlik eslatma scheduler (20:00 Toshkent vaqti)
    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Tashkent"))
    scheduler.add_job(
        send_daily_reminders,
        trigger="cron",
        hour=20,
        minute=0,
        args=[app.bot],
        id="daily_reminder",
        replace_existing=True,
    )
    # Haftalik hisobot (Dushanba ertalab 9:00)
    scheduler.add_job(
        send_weekly_reports,
        trigger="cron",
        day_of_week="mon",
        hour=9,
        minute=0,
        args=[app.bot],
        id="weekly_report",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("🔔 Scheduler ishga tushdi: kunlik 20:00 + haftalik Dushanba 9:00 (Asia/Tashkent)")

    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_post(webhook_path, lambda r: webhook_handler(r, app))

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🚀 Server started on port {PORT}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
