"""
💰 Oson Byudjet Telegram Bot — v5 (Multi-transaction qo'shildi)
=======================================================
- Supabase PostgreSQL database
- 7 kunlik bepul sinov
- To'lov tizimi
- Qarzlar ro'yxati
- Balanslar nazorati (tranzaksiya bilan bog'langan!)
- Ovoz orqali kiritish (OpenAI Whisper) — BIR NECHTA amaliyot bir ovozda  ← YANGI
- Chek rasm tahlili (GPT-4o-mini Vision)
- PDF hisobot
"""

import logging
import os
import asyncio
import asyncpg
import tempfile
import httpx
import io
import base64
import json
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

# Qo'llanma videosi (file_id /getfile komandasi orqali olinadi)
GUIDE_VIDEO_FILE_ID = os.environ.get("GUIDE_VIDEO_FILE_ID", "")

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

async def transcribe_voice(file_path: str) -> str:
    """Ovozni matnga aylantiradi.
    1. Avval gpt-4o-mini-transcribe (o'zbek tilini yaxshi biladi)
    2. Agar ishlamasa, whisper-1 ga qaytamiz (language siz)"""

    whisper_prompt = (
        "Bu o'zbek tilidagi moliyaviy ovoz xabari. "
        "Raqamlar: ming, million, so'm. "
        "Misol: Bozordan yuz ming so'm xarid qildim. "
        "Sardorga ikki yuz ming so'm qarz berdim."
    )

    # 1-urinish: gpt-4o-mini-transcribe (o'zbek tilini yaxshi biladi)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("voice.ogg", f, "audio/ogg")},
                    data={
                        "model": "gpt-4o-mini-transcribe",
                        "language": "uz",
                        "prompt": whisper_prompt,
                        "temperature": 0,
                    }
                )
            if response.status_code == 200:
                text = response.json().get("text", "")
                logger.info(f"🎤 gpt-4o-mini-transcribe: '{text}'")
                if text:
                    return text
            else:
                logger.warning(f"gpt-4o-mini-transcribe xato: {response.text}")
    except Exception as e:
        logger.warning(f"gpt-4o-mini-transcribe exception: {e}")

    # 2-urinish: whisper-1 (language siz, chunki uz ni qo'llab-quvvatlamaydi)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("voice.ogg", f, "audio/ogg")},
                    data={
                        "model": "whisper-1",
                        "prompt": whisper_prompt,
                        "temperature": 0,
                    }
                )
            if response.status_code == 200:
                text = response.json().get("text", "")
                logger.info(f"🎤 whisper-1 fallback: '{text}'")
                return text
            else:
                logger.error(f"whisper-1 ham xato: {response.text}")
                return ""
    except Exception as e:
        logger.error(f"Transcribe to'liq xato: {e}")
        return ""


async def parse_voice_transactions(text: str) -> list:
    """GPT-4o-mini orqali matndan BIR YOKI BIR NECHTA amaliyotni ajratadi.
    Har biri: income / expense / debt_gave / debt_took"""

    expense_cats = ", ".join(EXPENSE_CATEGORIES)
    income_cats = ", ".join(INCOME_CATEGORIES)

    system_prompt = (
        "Sen o'zbek tilidagi moliyaviy ovoz xabarlarini tahlil qiluvchi yordamchisan.\n"
        "Foydalanuvchi BITTA gapda BIR YOKI BIR NECHTA moliyaviy amaliyotni aytishi mumkin.\n"
        "Har bir alohida amaliyotni ajrat.\n\n"
        "MUHIM: Har doim shu formatda JSON qaytar:\n"
        '{\"transactions\": [ {...}, {...} ]}\n\n'
        "Har bir amaliyot uchun:\n"
        "1. type: 'income' | 'expense' | 'debt_gave' | 'debt_took'\n"
        "   - 'sarfladim/berdim/to'ladim/oldim (mahsulot)/xarjladim' = expense\n"
        "   - 'maosh/tushdi/kirdi/daromad/ishlab topdim' = income\n"
        "   - 'qarz berdim/qarzga berdim' = debt_gave\n"
        "   - 'qarz oldim/qarzga oldim' = debt_took\n"
        "2. amount: butun son (so'mda). 'ming'=1000, 'million'=1000000\n"
        "3. category: faqat expense/income uchun. Quyidagidan ANIQ BIRI:\n"
        f"   Xarajat: {expense_cats}\n"
        f"   Daromad: {income_cats}\n"
        "4. note: qisqa izoh (3-8 so'z)\n"
        "5. person: faqat debt uchun — kim (masalan 'Sardor'). Aks holda null\n\n"
        "MISOL kirish: 'bozordan 600 ming bozorlik qildim, mashinaga 200 ming "
        "yoqilg'i quydirdim, Sardorga 300 ming qarz berdim'\n"
        "MISOL chiqish:\n"
        '{\"transactions\": ['
        '{\"type\":\"expense\",\"amount\":600000,\"category\":\"🍔 Oziq-ovqat\",\"note\":\"Bozorlik\",\"person\":null},'
        '{\"type\":\"expense\",\"amount\":200000,\"category\":\"🚌 Transport\",\"note\":\"Yoqilg\'i\",\"person\":null},'
        '{\"type\":\"debt_gave\",\"amount\":300000,\"category\":null,\"note\":\"Qarz berildi\",\"person\":\"Sardor\"}'
        ']}\n\n'
        "Tushunarsiz bo'lsa: {\"transactions\": []}\n"
        "FAQAT JSON qaytar, boshqa hech narsa yozma!"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                }
            )

            if response.status_code != 200:
                logger.error(f"GPT error: {response.text}")
                return []

            content = response.json()["choices"][0]["message"]["content"]
            logger.info(f"🤖 GPT multi response: {content}")

            parsed = json.loads(content)
            raw_list = parsed.get("transactions", [])

            results = []
            for item in raw_list:
                ttype = item.get("type", "expense")
                if ttype not in ("income", "expense", "debt_gave", "debt_took"):
                    ttype = "expense"

                amount = float(item.get("amount", 0))
                if amount <= 0:
                    continue

                # Kategoriya validatsiyasi faqat income/expense uchun
                category = item.get("category") or ""
                if ttype == "expense":
                    if category not in EXPENSE_CATEGORIES:
                        category = "📦 Boshqa"
                elif ttype == "income":
                    if category not in INCOME_CATEGORIES:
                        category = "📦 Boshqa daromad"
                else:
                    category = None  # debt

                results.append({
                    "type": ttype,
                    "amount": amount,
                    "category": category,
                    "note": (item.get("note") or "")[:200],
                    "person": item.get("person"),
                })

            return results

    except Exception as e:
        logger.error(f"GPT multi parse error: {e}")
        return []


async def analyze_receipt_image(image_bytes: bytes) -> dict:
    """Chek rasmini GPT-4o-mini Vision orqali tahlil qiladi.
    type, amount, category, note qaytaradi."""

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    expense_cats = ", ".join(EXPENSE_CATEGORIES)

    system_prompt = (
        "Sen Oson Byudjet ilovasi uchun chek rasmini tahlil qiluvchi yordamchisan.\n\n"
        "Chek rasmidan quyidagi ma'lumotlarni aniqlab, FAQAT JSON formatida qaytar:\n\n"
        "1. amount: chekning UMUMIY summasi (raqam, so'mda)\n"
        "2. category: quyidagilardan ANIQ BIRINI tanlang:\n"
        f"   {expense_cats}\n"
        "3. merchant: savdo joyining nomi (masalan: Korzinka, Makro, Havas, Lola Bozori)\n"
        "4. note: qisqa izoh (savdo nomi + asosiy mahsulotlar, 5-10 so'z)\n"
        "5. confidence: 'high', 'medium' yoki 'low'\n\n"
        "QOIDALAR:\n"
        "- Chek rasmi emas bo'lsa: {\"error\": \"not_a_receipt\"}\n"
        "- Summa o'qib bo'lmasa: amount=0\n"
        "- Ruscha mahsulot nomlarini o'zbekchaga tarjima qil:\n"
        "  Хлеб→Non, Молоко→Sut, Яблоки→Olma, Мясо→Go'sht, Сахар→Shakar\n"
        "- Savdo turi:\n"
        "  • Supermarket/do'kon/bozor → '🍔 Oziq-ovqat'\n"
        "  • Dorixona/klinika → '💊 Salomatlik'\n"
        "  • Yoqilg'i/taksi → '🚌 Transport'\n"
        "  • Restoran/kafe → '🎮 Ko'ngil ochar'\n"
        "  • Kommunal to'lov → '💡 Kommunal'\n"
        "  • Aniqlanmasa → '📦 Boshqa'\n\n"
        "Misol javob: {\"amount\":247500,\"category\":\"🍔 Oziq-ovqat\","
        "\"merchant\":\"Korzinka Yunusobod\",\"note\":\"Korzinka: non, sut, olma\","
        "\"confidence\":\"high\"}"
    )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Bu chekni tahlil qil va JSON qaytar."
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_b64}"
                                    }
                                }
                            ]
                        }
                    ],
                    "temperature": 0.1,
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"},
                }
            )

            if response.status_code != 200:
                logger.error(f"GPT Vision error: {response.text}")
                return {"success": False, "error": "api_error"}

            content = response.json()["choices"][0]["message"]["content"]
            logger.info(f"📸 GPT receipt response: {content}")

            parsed = json.loads(content)

            if "error" in parsed:
                return {"success": False, "error": parsed["error"]}

            amount = float(parsed.get("amount", 0))
            if amount <= 0:
                return {"success": False, "error": "amount_not_detected"}

            category = parsed.get("category", "📦 Boshqa")
            if category not in EXPENSE_CATEGORIES:
                logger.warning(f"Invalid category from GPT: {category}")
                category = "📦 Boshqa"

            return {
                "success": True,
                "type": "expense",
                "amount": amount,
                "category": category,
                "merchant": parsed.get("merchant", ""),
                "note": parsed.get("note", "")[:200],
                "confidence": parsed.get("confidence", "medium"),
            }

    except Exception as e:
        logger.error(f"Receipt analysis error: {e}")
        return {"success": False, "error": f"exception: {str(e)}"}


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
        was_new = await conn.fetchrow(
            "SELECT telegram_id FROM users WHERE telegram_id = $1", telegram_id
        )
        await conn.execute("""
            INSERT INTO users (telegram_id, name, registered_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (telegram_id) DO UPDATE SET name = $2
        """, telegram_id, name)

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
            await conn.execute("""
                INSERT INTO transactions (telegram_id, type, amount, category, note, balance_id)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, telegram_id, txn_type, amount, category, note, balance_id)

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
    """/start ekranida qarz eslatmasi ko'rsatish uchun.
    30 kun ichida yoki kechikkan qarzlarni qaytaradi."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, person_name, amount, direction, due_date,
                   (due_date - CURRENT_DATE) AS days_left
            FROM debts
            WHERE telegram_id = $1
              AND is_paid = FALSE
              AND due_date IS NOT NULL
              AND (
                  (due_date - CURRENT_DATE) <= 30
              )
            ORDER BY due_date ASC
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

def generate_stats_pdf(user_name, stats, cat_stats, budget, month_str, transactions=None):
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
    cat_heading_style = ParagraphStyle(
        'CatHeading', parent=styles['Heading3'],
        fontSize=12, spaceAfter=8, spaceBefore=10,
        textColor=colors.HexColor('#2255A8')
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
        elements.append(Spacer(1, 0.7*cm))

    if transactions:
        cat_txns = {}
        for t in transactions:
            cat = t.get("category", "Boshqa")
            if cat not in cat_txns:
                cat_txns[cat] = []
            cat_txns[cat].append(t)

        expense_cats = sorted(
            [(c, ts) for c, ts in cat_txns.items() if any(t["type"] == "expense" for t in ts)],
            key=lambda x: -sum(float(t["amount"]) for t in x[1] if t["type"] == "expense")
        )

        if expense_cats:
            elements.append(Paragraph("Batafsil xarajatlar (izohlar bilan):", normal_style))
            elements.append(Spacer(1, 0.3*cm))

            for cat, txns in expense_cats:
                cat_total = sum(float(t["amount"]) for t in txns if t["type"] == "expense")
                elements.append(Paragraph(
                    f"{cat} — {cat_total:,.0f} so'm",
                    cat_heading_style
                ))

                detail_data = [["Sana", "Miqdor", "Izoh"]]
                for t in txns:
                    if t["type"] != "expense":
                        continue
                    date_str = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else str(t["date"])[:10]
                    note = t.get("note", "") or "—"
                    if len(note) > 50:
                        note = note[:47] + "..."
                    detail_data.append([
                        date_str,
                        f"{float(t['amount']):,.0f} so'm",
                        note
                    ])

                detail_table = Table(detail_data, colWidths=[2.5*cm, 4*cm, 10.5*cm])
                detail_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8EDF5')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#2255A8')),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('FONTSIZE', (0, 1), (-1, -1), 9),
                    ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                    ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FAFBFD')]),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ]))
                elements.append(detail_table)
                elements.append(Spacer(1, 0.3*cm))

        income_cats = sorted(
            [(c, ts) for c, ts in cat_txns.items() if any(t["type"] == "income" for t in ts)],
            key=lambda x: -sum(float(t["amount"]) for t in x[1] if t["type"] == "income")
        )

        if income_cats:
            elements.append(Spacer(1, 0.3*cm))
            elements.append(Paragraph("Batafsil daromadlar (izohlar bilan):", normal_style))
            elements.append(Spacer(1, 0.3*cm))

            for cat, txns in income_cats:
                cat_total = sum(float(t["amount"]) for t in txns if t["type"] == "income")
                elements.append(Paragraph(
                    f"{cat} — {cat_total:,.0f} so'm",
                    cat_heading_style
                ))

                detail_data = [["Sana", "Miqdor", "Izoh"]]
                for t in txns:
                    if t["type"] != "income":
                        continue
                    date_str = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else str(t["date"])[:10]
                    note = t.get("note", "") or "—"
                    if len(note) > 50:
                        note = note[:47] + "..."
                    detail_data.append([
                        date_str,
                        f"{float(t['amount']):,.0f} so'm",
                        note
                    ])

                detail_table = Table(detail_data, colWidths=[2.5*cm, 4*cm, 10.5*cm])
                detail_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8F5E9')),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#2E7D32')),
                    ('FONTSIZE', (0, 0), (-1, 0), 10),
                    ('FONTSIZE', (0, 1), (-1, -1), 9),
                    ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
                    ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
                    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F5FAF5')]),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                    ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ]))
                elements.append(detail_table)
                elements.append(Spacer(1, 0.3*cm))

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
        [InlineKeyboardButton("📖 Qo'llanma (video)", callback_data="guide")],
    ]
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
    if due_debts:
        msg = "🔔 <b>Qarz eslatmasi!</b>\n\n"
        overdue = [d for d in due_debts if d["days_left"] < 0]
        today_debts = [d for d in due_debts if d["days_left"] == 0]
        urgent = [d for d in due_debts if 0 < d["days_left"] <= 3]
        soon = [d for d in due_debts if 3 < d["days_left"] <= 14]
        future = [d for d in due_debts if d["days_left"] > 14]

        if overdue:
            msg += "⚠️ <b>KECHIKKAN:</b>\n"
            for d in overdue:
                msg += f"⛔️ {d['person_name']} — {format_money(float(d['amount']))} ({abs(d['days_left'])} kun kechikdi)\n"
            msg += "\n"

        if today_debts:
            msg += "🚨 <b>BUGUN:</b>\n"
            for d in today_debts:
                emoji = "🟢" if d["direction"] == "took" else "🔴"
                action = "qaytarishingiz" if d["direction"] == "took" else "olishingiz"
                msg += f"{emoji} {d['person_name']} — {format_money(float(d['amount']))} ({action})\n"
            msg += "\n"

        if urgent:
            msg += "🟠 <b>1-3 kun ichida:</b>\n"
            for d in urgent:
                msg += f"📅 {d['days_left']} kun ({d['due_date'].strftime('%d.%m')}) — {d['person_name']}: {format_money(float(d['amount']))}\n"
            msg += "\n"

        if soon:
            msg += "🟡 <b>1-2 hafta ichida:</b>\n"
            for d in soon:
                msg += f"📅 {d['days_left']} kun ({d['due_date'].strftime('%d.%m')}) — {d['person_name']}: {format_money(float(d['amount']))}\n"
            msg += "\n"

        if future:
            msg += "🔵 <b>Kelajakda (1 oy ichida):</b>\n"
            for d in future:
                msg += f"📅 {d['days_left']} kun ({d['due_date'].strftime('%d.%m')}) — {d['person_name']}: {format_money(float(d['amount']))}\n"

        await update.message.reply_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💸 Qarzlar", callback_data="debts")
            ]])
        )

    if is_new:
        welcome_text = (
            f"👋 Salom, <b>{user.first_name}</b>! Xush kelibsiz!\n\n"
            f"💰 <b>Oson Byudjet</b> — shaxsiy moliya yordamchingiz!\n\n"
            f"Bu bot bilan:\n"
            f"✅ Daromad va xarajatlarni yozing\n"
            f"✅ Ovoz orqali kiritish 🎤\n"
            f"✅ Chek rasmini yuboring — avtomatik tahlil 📸\n"
            f"✅ Oylik statistika va PDF hisobot\n"
            f"✅ Byudjet belgilang va nazorat qiling\n"
            f"✅ Qarzlarni kuzating\n"
            f"✅ Balanslar (Naqd, Karta) bilan bog'langan!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎁 <b>7 kun to'liq BEPUL!</b>\n"
            f"💡 Sizga avtomatik <b>Naqd</b> va <b>Karta</b> balanslari yaratildi.\n"
            f"💳 Balanslar bo'limidan miqdorni kiriting va boshlang!\n\n"
            f"📖 <b>Birinchi marta ishlatayapsizmi?</b>\n"
            f"Quyidagi tugma orqali videoqo'llanmani ko'ring! 👇"
        )
        await update.message.reply_text(
            welcome_text, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Qo'llanmani ko'rish", callback_data="guide")],
                [InlineKeyboardButton("🚀 Boshlash!", callback_data="back_main")]
            ])
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
    text += "\n🎤 Ovoz yuboring, 📸 chek rasmini yuboring yoki tugma bosing:"
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

    transactions = await parse_voice_transactions(text)

    if not transactions:
        await msg.edit_text(
            f"🎤 <b>Tanildi:</b> {text}\n\n"
            f"❌ Amaliyot aniqlanmadi.\n"
            f"<i>Masalan: 'Non uchun 5000 so'm'</i>",
            parse_mode="HTML"
        )
        return

    # Qarzlarni darhol saqlaymiz (balans talab qilmaydi)
    debts = [t for t in transactions if t["type"] in ("debt_gave", "debt_took")]
    money_txns = [t for t in transactions if t["type"] in ("income", "expense")]

    debt_summary = ""
    for d in debts:
        direction = "gave" if d["type"] == "debt_gave" else "took"
        await add_debt(
            user_id,
            person_name=d.get("person") or "Noma'lum",
            amount=d["amount"],
            direction=direction,
            due_date=None,
            note=d.get("note", "")
        )
        emoji = "🔴" if direction == "gave" else "🟢"
        action = "berdim" if direction == "gave" else "oldim"
        debt_summary += f"{emoji} Qarz {action}: {d.get('person') or '—'} — {format_money(d['amount'])}\n"

    # Agar faqat qarz bo'lsa
    if not money_txns:
        await msg.edit_text(
            f"🎤 <b>Tanildi:</b> {text}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ <b>Qarz(lar) saqlandi!</b>\n\n{debt_summary}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💸 Qarzlar", callback_data="debts"),
                InlineKeyboardButton("🏠 Menyu", callback_data="back_main")
            ]])
        )
        return

    # Pul amaliyotlarini balans tanlashga saqlaymiz
    context.user_data["pending_txns"] = money_txns
    context.user_data["pending_text"] = text

    # Xulosa ko'rsatish
    summary = f"🎤 <b>Tanildi:</b> {text}\n\n━━━━━━━━━━━━━━━━━━━━\n"
    if debt_summary:
        summary += debt_summary + "\n"
    summary += f"📋 <b>{len(money_txns)} ta amaliyot topildi:</b>\n\n"
    for i, t in enumerate(money_txns, 1):
        emoji = "📥" if t["type"] == "income" else "📤"
        summary += f"{i}. {emoji} {format_money(t['amount'])} — {t['category']}\n   <i>{t['note']}</i>\n"
    summary += "\nHammasi qaysi balansga?"

    bals = await get_balances(user_id)
    await msg.edit_text(
        summary,
        parse_mode="HTML",
        reply_markup=balance_select_keyboard(bals)
    )


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chek rasmi qabul qilib, GPT Vision orqali tahlil qiladi."""
    user_id = update.effective_user.id

    premium = await is_user_premium(user_id)
    if not premium:
        await show_payment_screen(update, context)
        return

    msg = await update.message.reply_text(
        "📸 Chek qabul qilindi\n"
        "🤖 Tahlil qilinmoqda... (5-10 soniya)"
    )

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    image_bytes_io = await file.download_as_bytearray()
    image_bytes = bytes(image_bytes_io)

    if len(image_bytes) > 10 * 1024 * 1024:
        await msg.edit_text(
            "❌ Rasm hajmi juda katta (10MB dan ortiq).\n"
            "Kichikroq rasm yuboring."
        )
        return

    result = await analyze_receipt_image(image_bytes)

    if not result["success"]:
        error_messages = {
            "not_a_receipt": (
                "❌ Bu chek emas ko'rinadi.\n\n"
                "Iltimos, do'kon yoki to'lov chekining aniq rasmini yuboring."
            ),
            "amount_not_detected": (
                "❌ Chek summasini aniqlab bo'lmadi.\n\n"
                "Rasm sifati past bo'lishi mumkin. Yorug' joyda, "
                "to'g'ri burchakdan qaytadan suratga oling."
            ),
            "api_error": "❌ Tahlil xizmatida xatolik. Birozdan keyin urinib ko'ring.",
        }
        error_text = error_messages.get(
            result["error"],
            f"❌ Xatolik yuz berdi. Qaytadan urinib ko'ring."
        )
        await msg.edit_text(error_text)
        return

    context.user_data["receipt_parsed"] = result

    confidence_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
    conf_emoji = confidence_emoji.get(result["confidence"], "⚪")

    merchant_text = f"\n🏪 Savdo: <b>{result['merchant']}</b>" if result.get("merchant") else ""

    confirmation_text = (
        f"🧾 <b>Chek tahlili tayyor</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📤 Tur: <b>Xarajat</b>{merchant_text}\n"
        f"💰 Summa: <b>{format_money(result['amount'])}</b>\n"
        f"🏷 Kategoriya: <b>{result['category']}</b>\n"
        f"📝 Izoh: <i>{result['note']}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{conf_emoji} Ishonch: {result['confidence']}\n\n"
        f"Tasdiqlaysizmi?"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Tasdiqlash", callback_data="receipt_confirm"),
            InlineKeyboardButton("✏️ Tahrirlash", callback_data="receipt_edit"),
        ],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="receipt_cancel")],
    ])

    await msg.edit_text(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Yordam — Oson Byudjet</b>\n\n"
        "/start — Bosh menyu\n/help — Yordam\n\n"
        "➕ Daromad/Xarajat kiritish\n"
        "🎤 Ovoz orqali kiritish (bir nechta amaliyot ham)\n"
        "📸 Chek rasm orqali kiritish\n"
        "💳 Balanslar bilan bog'langan\n"
        "📊 Statistika va PDF hisobot\n"
        "💸 Qarzlar ro'yxati\n"
        "🎯 Byudjet belgilash\n\n"
        "📖 Qo'llanma videosi — menyudan",
        parse_mode="HTML"
    )

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin video yuborsa, file_id ni qaytaradi (qo'llanma videosini sozlash uchun)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return

    video = update.message.video or update.message.document
    if not video:
        return

    file_id = video.file_id
    file_size = getattr(video, "file_size", 0) or 0
    duration = getattr(video, "duration", 0) or 0

    await update.message.reply_text(
        f"✅ <b>Video qabul qilindi!</b>\n\n"
        f"📹 Davomiyligi: <b>{duration} sek</b>\n"
        f"💾 Hajmi: <b>{file_size / 1024 / 1024:.1f} MB</b>\n\n"
        f"🔑 <b>file_id:</b>\n"
        f"<code>{file_id}</code>\n\n"
        f"📋 <i>Yuqoridagi file_id ni nusxalab, Render'da\n"
        f"GUIDE_VIDEO_FILE_ID environment variable'ga qo'ying.</i>",
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

    async def safe_edit(text, **kwargs):
        try:
            await query.edit_message_text(text, **kwargs)
        except Exception:
            await context.bot.send_message(
                chat_id=user_id, text=text, **kwargs
            )

    # ---------- TO'LOV BLOKLARI (premiumdan oldin) ----------
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

    # ---------- PREMIUM TEKSHIRUVI ----------
    premium = await is_user_premium(user_id)
    if not premium:
        await show_payment_screen(update, context)
        return

    # ---------- ASOSIY HANDLERLAR ----------
    if data == "add_income":
        context.user_data["txn_type"] = "income"
        await safe_edit(
            "📥 <b>Daromad kategoriyasini tanlang:</b>",
            parse_mode="HTML",
            reply_markup=category_keyboard(INCOME_CATEGORIES, "income"))

    elif data == "add_expense":
        context.user_data["txn_type"] = "expense"
        await safe_edit(
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
        bals = await get_balances(user_id)
        emoji = "📥" if txn_type == "income" else "📤"
        action = "qaysi balansga tushadi" if txn_type == "income" else "qaysi balansdan chiqadi"
        await query.edit_message_text(
            f"{emoji} <b>Kategoriya:</b> {category}\n\n"
            f"💳 Pul {action}?",
            parse_mode="HTML",
            reply_markup=balance_select_keyboard(bals))

    # ---------- BALANS TANLASH (yagona, birlashtirilgan) ----------
    elif data.startswith("selbal_"):
        balance_id = int(data.split("_")[1])

        # 1) Multi-transaction (ovozdan kelgan bir nechta amaliyot)
        if context.user_data.get("pending_txns"):
            pending = context.user_data.pop("pending_txns")
            context.user_data.pop("pending_text", None)

            for t in pending:
                await add_transaction(
                    user_id, t["type"], t["amount"],
                    t["category"], t.get("note", ""), balance_id
                )

            txns  = await get_month_transactions(user_id)
            stats = calc_stats(txns)

            saved = ""
            for t in pending:
                emoji = "📥" if t["type"] == "income" else "📤"
                saved += f"{emoji} {format_money(t['amount'])} — {t['category']}\n"

            await query.edit_message_text(
                f"✅ <b>{len(pending)} ta amaliyot saqlandi!</b>\n\n"
                f"{saved}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 {format_money(stats['income'])}  "
                f"📤 {format_money(stats['expenses'])}  "
                f"💵 {format_money(stats['balance'])}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
                     InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
                    [InlineKeyboardButton("📊 Statistika", callback_data="stats")],
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
                ])
            )
            return

        # 2) Bitta amaliyot (voice_parsed — chek tasdiqi ham shu yerga keladi)
        if context.user_data.get("voice_parsed"):
            parsed = context.user_data["voice_parsed"]
            await add_transaction(
                user_id, parsed["type"], parsed["amount"],
                parsed["category"], parsed.get("note", parsed.get("text", "")), balance_id
            )
            context.user_data.pop("voice_parsed", None)

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
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
                     InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
                    [InlineKeyboardButton("📊 Statistika", callback_data="stats")],
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
                ])
            )
            return

        # 3) Qo'lda kiritish (cat_ tugmasidan keyin — miqdor so'raladi)
        context.user_data["balance_id"] = balance_id
        context.user_data["awaiting_amount"] = True
        emoji = "📥" if context.user_data.get("txn_type") == "income" else "📤"
        await query.edit_message_text(
            f"{emoji} <b>Kategoriya:</b> {context.user_data.get('category')}\n\n"
            f"💬 Miqdorni kiriting (faqat raqam):\n<i>Masalan: 50000</i>",
            parse_mode="HTML")
        return

    # ---------- CHEK TAHLILI ----------
    elif data == "receipt_confirm":
        parsed = context.user_data.get("receipt_parsed")
        if not parsed:
            await query.edit_message_text("❌ Ma'lumot topilmadi. /start bosing.")
            return

        context.user_data["voice_parsed"] = {
            "type": parsed["type"],
            "amount": parsed["amount"],
            "category": parsed["category"],
            "note": parsed["note"],
            "text": parsed.get("merchant", "Chek tahlili"),
        }
        context.user_data.pop("receipt_parsed", None)

        bals = await get_balances(user_id)
        await query.edit_message_text(
            f"✅ Tasdiqlandi\n\n"
            f"📤 {format_money(parsed['amount'])}\n"
            f"🏷 {parsed['category']}\n\n"
            f"💳 Qaysi balansdan chiqdi?",
            parse_mode="HTML",
            reply_markup=balance_select_keyboard(bals)
        )

    elif data == "receipt_cancel":
        context.user_data.pop("receipt_parsed", None)
        await query.edit_message_text(
            "❌ Chek tahlili bekor qilindi.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")
            ]])
        )

    elif data == "receipt_edit":
        parsed = context.user_data.get("receipt_parsed")
        if not parsed:
            await query.edit_message_text("❌ Ma'lumot topilmadi. /start bosing.")
            return

        await query.edit_message_text(
            f"✏️ <b>Nimani tahrirlamoqchisiz?</b>\n\n"
            f"💰 Summa: {format_money(parsed['amount'])}\n"
            f"🏷 Kategoriya: {parsed['category']}\n"
            f"📝 Izoh: {parsed['note']}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Summa", callback_data="receipt_edit_amount")],
                [InlineKeyboardButton("🏷 Kategoriya", callback_data="receipt_edit_category")],
                [InlineKeyboardButton("📝 Izoh", callback_data="receipt_edit_note")],
                [
                    InlineKeyboardButton("✅ Saqlash", callback_data="receipt_confirm"),
                    InlineKeyboardButton("❌ Bekor", callback_data="receipt_cancel"),
                ],
            ])
        )

    elif data == "receipt_edit_amount":
        context.user_data["awaiting_receipt_amount"] = True
        await query.edit_message_text(
            "💰 Yangi summani kiriting:\n<i>Masalan: 247500</i>",
            parse_mode="HTML"
        )

    elif data == "receipt_edit_category":
        buttons, row = [], []
        for i, cat in enumerate(EXPENSE_CATEGORIES):
            row.append(InlineKeyboardButton(cat, callback_data=f"rcat_{i}"))
            if len(row) == 2:
                buttons.append(row); row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="receipt_edit")])

        await query.edit_message_text(
            "🏷 <b>Yangi kategoriyani tanlang:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("rcat_"):
        idx = int(data.split("_")[1])
        new_category = EXPENSE_CATEGORIES[idx]
        parsed = context.user_data.get("receipt_parsed", {})
        parsed["category"] = new_category
        context.user_data["receipt_parsed"] = parsed

        confidence_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}
        conf_emoji = confidence_emoji.get(parsed.get("confidence", "medium"), "⚪")
        merchant_text = f"\n🏪 Savdo: <b>{parsed.get('merchant', '')}</b>" if parsed.get("merchant") else ""

        await query.edit_message_text(
            f"🧾 <b>Chek tahlili (tahrirlangan)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📤 Tur: <b>Xarajat</b>{merchant_text}\n"
            f"💰 Summa: <b>{format_money(parsed['amount'])}</b>\n"
            f"🏷 Kategoriya: <b>{parsed['category']}</b> ✏️\n"
            f"📝 Izoh: <i>{parsed['note']}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{conf_emoji} Ishonch: {parsed.get('confidence', 'medium')}\n\n"
            f"Tasdiqlaysizmi?",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Tasdiqlash", callback_data="receipt_confirm"),
                    InlineKeyboardButton("✏️ Yana tahrir", callback_data="receipt_edit"),
                ],
                [InlineKeyboardButton("❌ Bekor", callback_data="receipt_cancel")],
            ])
        )

    elif data == "receipt_edit_note":
        context.user_data["awaiting_receipt_note"] = True
        await query.edit_message_text(
            "📝 Yangi izohni kiriting:",
            parse_mode="HTML"
        )

    # ---------- STATISTIKA ----------
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

        await safe_edit(msg, parse_mode="HTML",
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

        pdf_bytes = generate_stats_pdf(user_name, stats, cat_stats, budget, month_str, transactions=txns)

        await context.bot.send_document(
            chat_id=user_id,
            document=io.BytesIO(pdf_bytes),
            filename=f"hisobot_{datetime.now().strftime('%Y_%m')}.pdf",
            caption=f"📄 <b>{month_str} hisoboti</b>\n\nDavom etish uchun tugma bosing 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
                 InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
                [InlineKeyboardButton("📊 Statistika", callback_data="stats")],
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
            ])
        )

    # ---------- TARIX ----------
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
                date_str = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else str(t["date"])[:10]
                bal   = f" | 💳 {t['balance_name']}" if t.get("balance_name") else ""
                note  = f" — {t['note']}" if t.get("note") else ""
                msg  += f"{emoji} <b>{format_money(float(t['amount']))}</b>\n   📁 {t.get('category','Boshqa')}{bal} | 📅 {date_str}{note}\n\n"

        await query.edit_message_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Oylar", callback_data="history"),
                InlineKeyboardButton("🏠 Menyu", callback_data="back_main")]]))

    # ---------- QARZLAR ----------
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

    # ---------- BALANSLAR ----------
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

    # ---------- BUDGET / TOZALASH ----------
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

    # ---------- ADMIN PANEL ----------
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
            [InlineKeyboardButton("💸 Qarz eslatmasi (test)", callback_data="admin_send_debt")],
            [InlineKeyboardButton("📊 Haftalik hisobot (test)", callback_data="admin_send_weekly")],
            [InlineKeyboardButton("💳 Barchaga balans qo'shish", callback_data="admin_fix_balances")],
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

    elif data == "admin_send_debt":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "💸 <b>Qarz eslatmalari yuborilmoqda...</b>\n\n"
            "Bugun yoki 3 kun ichida qaytarish kerak bo'lganlarga eslatma boradi.",
            parse_mode="HTML"
        )
        await send_debt_reminders(context.bot)
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ <b>Qarz eslatmalari yuborildi!</b>\n\nAgar hech kim olmagan bo'lsa — demak bugun yoki yaqin kunlarda qarz yo'q.",
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

    elif data == "admin_fix_balances":
        if user_id != ADMIN_ID:
            return
        await query.edit_message_text(
            "💳 <b>Tekshirilmoqda...</b>\n\n"
            "Balansi yo'q foydalanuvchilarga Naqd va Karta qo'shiladi.",
            parse_mode="HTML"
        )
        async with db_pool.acquire() as conn:
            users_without_balance = await conn.fetch("""
                SELECT u.telegram_id, u.name
                FROM users u
                WHERE NOT EXISTS (
                    SELECT 1 FROM balances b WHERE b.telegram_id = u.telegram_id
                )
            """)

            added = 0
            for u in users_without_balance:
                await conn.execute("""
                    INSERT INTO balances (telegram_id, name, type, amount)
                    VALUES ($1, 'Naqd', 'cash', 0), ($1, 'Karta', 'card', 0)
                """, u["telegram_id"])
                added += 1

        await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ <b>Bajarildi!</b>\n\n"
                f"💳 Balans qo'shildi: <b>{added}</b> ta foydalanuvchiga\n"
                f"(har biriga Naqd va Karta = jami {added*2} ta balans)\n\n"
                f"Endi ular bot'ni to'liq ishlata oladi!"
            ),
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

    # ---------- QO'LLANMA ----------
    elif data == "guide":
        if not GUIDE_VIDEO_FILE_ID:
            await query.edit_message_text(
                "📖 <b>Qo'llanma</b>\n\n"
                "⚠️ Video hali yuklanmagan.\n"
                "Admin bilan bog'laning.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")
                ]])
            )
            return

        caption = (
            "📖 <b>Botni ishlatish bo'yicha qo'llanma</b>\n\n"
            "Ushbu videoda ko'rsatilgan:\n"
            "✅ Xarajat va daromad qo'shish\n"
            "✅ Balansni boshqarish\n"
            "✅ Ovoz orqali kiritish 🎤\n"
            "✅ Statistika va PDF hisobot\n\n"
            "<i>Savollar bo'lsa: @elyorbek_tech</i>"
        )
        try:
            await context.bot.send_video(
                chat_id=user_id,
                video=GUIDE_VIDEO_FILE_ID,
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")
                ]])
            )
        except Exception as e:
            logger.error(f"❌ Qo'llanma video yuborilmadi: {e}")
            await query.message.reply_text(
                "❌ Video yuborilmadi. Qaytadan urinib ko'ring.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")
                ]])
            )
        return

    elif data == "back_main":
        txns  = await get_month_transactions(user_id)
        stats = calc_stats(txns)
        await safe_edit(
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
        context.user_data.get("awaiting_receipt_amount"),
        context.user_data.get("awaiting_receipt_note"),
    ]):
        premium = await is_user_premium(user_id)
        if not premium:
            await show_payment_screen(update, context)
            return
        await update.message.reply_text("👇 Boshlash uchun /start yuboring.")
        return

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

    elif context.user_data.get("awaiting_receipt_amount"):
        try:
            amount = float(text.replace(" ", "").replace(",", ""))
            if amount <= 0:
                raise ValueError
            parsed = context.user_data.get("receipt_parsed", {})
            parsed["amount"] = amount
            context.user_data["receipt_parsed"] = parsed
            context.user_data.pop("awaiting_receipt_amount")

            await update.message.reply_text(
                f"✅ Summa o'zgartirildi: <b>{format_money(amount)}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Tasdiqlash", callback_data="receipt_confirm")],
                    [InlineKeyboardButton("✏️ Yana tahrir", callback_data="receipt_edit")],
                    [InlineKeyboardButton("❌ Bekor", callback_data="receipt_cancel")],
                ])
            )
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

    elif context.user_data.get("awaiting_receipt_note"):
        parsed = context.user_data.get("receipt_parsed", {})
        parsed["note"] = text[:200]
        context.user_data["receipt_parsed"] = parsed
        context.user_data.pop("awaiting_receipt_note")

        await update.message.reply_text(
            f"✅ Izoh yangilandi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Tasdiqlash", callback_data="receipt_confirm")],
                [InlineKeyboardButton("✏️ Yana tahrir", callback_data="receipt_edit")],
                [InlineKeyboardButton("❌ Bekor", callback_data="receipt_cancel")],
            ])
        )

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
       
