"""
💰 Oson Byudjet Telegram Bot — v6 (Tranzaksiyani tahrirlash/o'chirish qo'shildi)
=======================================================
- Supabase PostgreSQL database
- 7 kunlik bepul sinov
- To'lov tizimi
- Qarzlar ro'yxati
- Balanslar nazorati (tranzaksiya bilan bog'langan!)
- Ovoz orqali kiritish (OpenAI Whisper) — BIR NECHTA amaliyot bir ovozda
- Chek rasm tahlili (GPT-4o-mini Vision)
- PDF hisobot
- Tranzaksiyani tahrirlash va o'chirish (balans avtomatik tiklanadi)  ← YANGI
"""

import logging
import os
import sys
import asyncio
import asyncpg
import tempfile
import httpx
import io
import base64
import json
import secrets
import calendar as cal_module
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

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _MPL_OK = True
except ImportError:
    _MPL_OK = False

# ===================== SOZLAMALAR =====================
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "")
PORT           = int(os.environ.get("PORT", 8080))
DATABASE_URL   = os.environ.get("DATABASE_URL", "")
ADMIN_ID       = int(os.environ.get("ADMIN_ID", "8008645253"))
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DASHBOARD_URL  = os.environ.get("DASHBOARD_URL", "")

# Qo'llanma videosi (file_id /getfile komandasi orqali olinadi)
GUIDE_VIDEO_FILE_ID = os.environ.get("GUIDE_VIDEO_FILE_ID", "")

PRICE_MONTHLY   = 25000
PRICE_QUARTERLY = 60000
PRICE_YEARLY    = 199000

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
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
    """Ovozni matnga aylantiradi (whisper-1 + tr til kodi)."""

    whisper_prompt = (
        "Bu o'zbek tilidagi moliyaviy ovoz xabari. "
        "Raqamlar so'mda: ming, ikki ming, besh ming, o'n ming, "
        "ellik ming, yuz ming, ikki yuz ming, uch yuz ming, "
        "to'rt yuz ming, besh yuz ming, olti yuz ming, million. "
        "Misol: Bozordan olti yuz ming so'mga xarid qildim. "
        "Sardorga to'rt yuz ming so'm qarz berdim. "
        "Mashinaga ikki yuz ming yoqilg'i quydirdim."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("voice.ogg", f, "audio/ogg")},
                    data={
                        "model": "whisper-1",
                        "language": "uz",  # avval uz bilan urinib ko'ramiz
                        "prompt": whisper_prompt,
                        "temperature": 0,
                    }
                )
            if response.status_code == 200:
                text = response.json().get("text", "")
                logger.info(f"🎤 whisper-1 (uz): '{text}'")
                if text and len(text.strip()) > 3:
                    return text
            else:
                logger.warning(f"whisper-1 uz xato: {response.text}")
    except Exception as e:
        logger.warning(f"whisper-1 uz exception: {e}")

    # Fallback: turk tili bilan urinib ko'ramiz (akustik o'xshashlik)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                response = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    files={"file": ("voice.ogg", f, "audio/ogg")},
                    data={
                        "model": "whisper-1",
                        "language": "tr",
                        "prompt": whisper_prompt,
                        "temperature": 0,
                    }
                )
            if response.status_code == 200:
                text = response.json().get("text", "")
                logger.info(f"🎤 whisper-1 (tr fallback): '{text}'")
                return text
            else:
                logger.error(f"whisper-1 tr ham xato: {response.text}")
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
        "2. amount: butun son (so'mda). RAQAMLAR JADVALI:\n"
        "   - 'ming' = 1000\n"
        "   - 'ikki ming' = 2000\n"
        "   - 'besh ming' = 5000\n"
        "   - 'o'n ming' = 10000\n"
        "   - 'ellik ming' = 50000\n"
        "   - 'yuz ming' = 100000\n"
        "   - 'ikki yuz ming' / 'ikiz min' / 'iki yuz min' = 200000\n"
        "   - 'uch yuz ming' / 'üçüz min' / 'uchz min' = 300000\n"
        "   - 'to'rt yuz ming' / 'dörüz min' / 'dört yüz min' = 400000\n"
        "   - 'besh yuz ming' / 'beşüz min' = 500000\n"
        "   - 'olti yuz ming' / 'altıyüz min' = 600000\n"
        "   - 'million' / 'milyon' = 1000000\n"
        "   MUHIM: Whisper turkcha-ozarbayjon tilida transkripsiya qilishi mumkin. "
        "   'üçüz min' = uch yuz ming (300000), 'dörüz min' = to'rt yuz ming (400000), "
        "   'beşüz min' = besh yuz ming (500000). Bu xato emas, transliteratsiya.\n"
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
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=5,
        connect_timeout=30,
        command_timeout=60,
    )
    async with db_pool.acquire() as conn:
        # DDL migrations uchun server-side statement timeout o'chiriladi
        await conn.execute("SET statement_timeout = 0")
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mcp_tokens (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
                token      TEXT UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        try:
            await conn.execute("""
                ALTER TABLE debts ADD COLUMN IF NOT EXISTS
                    balance_id INTEGER REFERENCES balances(id) ON DELETE SET NULL
            """)
        except Exception as e:
            logger.warning(f"ALTER TABLE debts (balance_id): {e}")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS login_codes (
                id         SERIAL PRIMARY KEY,
                user_id    BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
                code       TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used       BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
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
                          balance_id: int = None) -> int:
    """Tranzaksiya qo'shish + balansni yangilash. id qaytaradi."""
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tx_id = await conn.fetchval("""
                INSERT INTO transactions (telegram_id, type, amount, category, note, balance_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
            """, telegram_id, txn_type, amount, category, note, balance_id)

            if balance_id:
                if txn_type == "income":
                    await conn.execute(
                        "UPDATE balances SET amount = amount + $1 WHERE id = $2",
                        amount, balance_id)
                else:
                    await conn.execute(
                        "UPDATE balances SET amount = amount - $1 WHERE id = $2",
                        amount, balance_id)
            return tx_id

async def get_transaction(telegram_id: int, tx_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, type, amount, category, note, balance_id "
            "FROM transactions WHERE id = $1 AND telegram_id = $2",
            tx_id, telegram_id)

async def delete_transaction(telegram_id: int, tx_id: int) -> bool:
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT type, amount, balance_id FROM transactions "
                "WHERE id = $1 AND telegram_id = $2", tx_id, telegram_id)
            if not tx:
                return False
            # balans effektini orqaga qaytaramiz
            if tx["balance_id"]:
                if tx["type"] == "income":
                    await conn.execute("UPDATE balances SET amount = amount - $1 WHERE id = $2",
                                       tx["amount"], tx["balance_id"])
                else:
                    await conn.execute("UPDATE balances SET amount = amount + $1 WHERE id = $2",
                                       tx["amount"], tx["balance_id"])
            await conn.execute("DELETE FROM transactions WHERE id = $1 AND telegram_id = $2",
                               tx_id, telegram_id)
            return True

async def update_transaction(telegram_id: int, tx_id: int,
                             new_amount=None, new_type=None, new_category=None) -> bool:
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow(
                "SELECT type, amount, category, balance_id FROM transactions "
                "WHERE id = $1 AND telegram_id = $2", tx_id, telegram_id)
            if not tx:
                return False

            old_type, old_amt, bal_id = tx["type"], float(tx["amount"]), tx["balance_id"]
            f_type = new_type or old_type
            f_amt  = float(new_amount) if new_amount is not None else old_amt
            f_cat  = new_category if new_category is not None else tx["category"]

            if bal_id:
                # eski effektni qaytar
                if old_type == "income":
                    await conn.execute("UPDATE balances SET amount = amount - $1 WHERE id = $2", old_amt, bal_id)
                else:
                    await conn.execute("UPDATE balances SET amount = amount + $1 WHERE id = $2", old_amt, bal_id)
                # yangi effektni qo'lla
                if f_type == "income":
                    await conn.execute("UPDATE balances SET amount = amount + $1 WHERE id = $2", f_amt, bal_id)
                else:
                    await conn.execute("UPDATE balances SET amount = amount - $1 WHERE id = $2", f_amt, bal_id)

            await conn.execute(
                "UPDATE transactions SET type = $1, amount = $2, category = $3 "
                "WHERE id = $4 AND telegram_id = $5",
                f_type, f_amt, f_cat, tx_id, telegram_id)
            return True

async def create_login_code(telegram_id: int) -> str:
    code = str(secrets.randbelow(900000) + 100000)
    expires_at = datetime.now() + timedelta(minutes=10)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM login_codes WHERE user_id = $1 AND used = FALSE", telegram_id
        )
        await conn.execute("""
            INSERT INTO login_codes (user_id, code, expires_at)
            VALUES ($1, $2, $3)
        """, telegram_id, code, expires_at)
    return code

async def get_recent_transactions(telegram_id: int, limit: int = 8):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, type, amount, category, note, date FROM transactions "
            "WHERE telegram_id = $1 ORDER BY date DESC LIMIT $2", telegram_id, limit)
        return [dict(r) for r in rows]

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

async def get_transactions_by_date_range(telegram_id: int, start_date, end_date) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT t.type, t.amount, t.category, t.note, t.date, b.name AS balance_name
            FROM transactions t
            LEFT JOIN balances b ON t.balance_id = b.id
            WHERE t.telegram_id = $1
              AND DATE(t.date AT TIME ZONE 'Asia/Tashkent') >= $2
              AND DATE(t.date AT TIME ZONE 'Asia/Tashkent') <= $3
            ORDER BY t.date DESC
        """, telegram_id, start_date, end_date)
        return [dict(r) for r in rows]

async def clear_month_transactions(telegram_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM transactions
            WHERE telegram_id = $1
              AND DATE_TRUNC('month', date) = DATE_TRUNC('month', NOW())
        """, telegram_id)

async def add_debt(telegram_id: int, person_name: str, amount: float,
                   direction: str, due_date=None, note: str = "", balance_id: int = None):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO debts (telegram_id, person_name, amount, direction, due_date, note, balance_id)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, telegram_id, person_name, amount, direction, due_date, note, balance_id)
            if balance_id:
                delta = -amount if direction == "gave" else amount
                await conn.execute(
                    "UPDATE balances SET amount = amount + $1 WHERE id = $2 AND telegram_id = $3",
                    delta, balance_id, telegram_id
                )

async def get_debts(telegram_id: int) -> list:
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, person_name, amount, direction, due_date, is_paid, note, created_at, balance_id
            FROM debts
            WHERE telegram_id = $1 AND is_paid = FALSE
            ORDER BY due_date ASC NULLS LAST, created_at DESC
        """, telegram_id)
        return [dict(r) for r in rows]

async def mark_debt_paid(debt_id: int, return_balance_id: int = None):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            debt = await conn.fetchrow(
                "SELECT amount, direction FROM debts WHERE id = $1", debt_id
            )
            await conn.execute("UPDATE debts SET is_paid = TRUE WHERE id = $1", debt_id)
            if return_balance_id and debt:
                # "gave" → they return → I receive → add; "took" → I return → I pay → deduct
                delta = float(debt["amount"]) if debt["direction"] == "gave" else -float(debt["amount"])
                await conn.execute(
                    "UPDATE balances SET amount = amount + $1 WHERE id = $2",
                    delta, return_balance_id
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

async def create_mcp_token(telegram_id: int) -> str:
    token      = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=24)
    async with db_pool.acquire() as conn:
        # Clean up expired tokens for this user first
        await conn.execute(
            "DELETE FROM mcp_tokens WHERE user_id = $1 AND expires_at <= NOW()",
            telegram_id
        )
        await conn.execute(
            "INSERT INTO mcp_tokens (user_id, token, expires_at) VALUES ($1, $2, $3)",
            telegram_id, token, expires_at
        )
    return token

async def validate_mcp_token(token: str) -> "int | None":
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id FROM mcp_tokens WHERE token = $1 AND expires_at > NOW()",
            token
        )
    return row["user_id"] if row else None

# ===================== DONUT CHART =====================

def generate_donut_chart(
    cat_stats: dict,
    total_expenses: float,
    total_income: float,
    period_label: str = "",
) -> "io.BytesIO | None":
    if not _MPL_OK or not cat_stats or total_expenses <= 0:
        return None

    PALETTE = [
        '#FF6B6B', '#4ECDC4', '#45B7D1', '#A8E6CF', '#FFD93D',
        '#C3A6FF', '#FF9A9E', '#A1C4FD', '#96E6A1', '#FFECD2',
        '#FD7F6F', '#B2E4FF',
    ]
    BG, FG, SUB = '#111827', '#FFFFFF', '#9CA3AF'

    sorted_cats = sorted(cat_stats.items(), key=lambda x: -x[1])
    sizes  = [c[1] for c in sorted_cats]
    n      = len(sorted_cats)
    colors = PALETTE[:n]

    donut_h  = 4.0
    legend_h = 0.42 * n
    total_h  = donut_h + legend_h

    fig = plt.figure(figsize=(5.5, total_h), facecolor=BG)
    leg_frac   = legend_h / total_h
    donut_frac = donut_h  / total_h

    # ── Donut ─────────────────────────────────────────
    ax = fig.add_axes([0.05, leg_frac, 0.9, donut_frac])
    ax.set_facecolor(BG)
    ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        wedgeprops=dict(width=0.50, edgecolor=BG, linewidth=3),
        counterclock=False,
    )
    ax.text(0,  0.10, f"-{total_expenses:,.0f}",
            ha='center', va='center', fontsize=14, fontweight='bold', color=FG)
    ax.text(0, -0.20, "UZS",
            ha='center', va='center', fontsize=9, color=SUB)
    if period_label:
        ax.text(0.5, 1.0, period_label,
                ha='center', va='bottom', fontsize=8, color=SUB,
                transform=ax.transAxes)
    ax.axis('off')

    # ── Legend ────────────────────────────────────────
    ax_l = fig.add_axes([0.0, 0.0, 1.0, leg_frac])
    ax_l.set_facecolor(BG)
    ax_l.set_xlim(0, 1)
    ax_l.set_ylim(0, 1)
    ax_l.axis('off')

    for i, (cat, amt) in enumerate(sorted_cats):
        pct = int(amt / total_expenses * 100) if total_expenses else 0
        yc  = 1.0 - (i + 0.5) / n
        ax_l.plot([0.04], [yc], 'o',
                  color=colors[i], markersize=9,
                  transform=ax_l.transAxes, markeredgewidth=0)
        cat_text = cat.split(' ', 1)[-1] if ' ' in cat else cat
        ax_l.text(0.09, yc, cat_text,
                  ha='left', va='center', fontsize=8, color=FG,
                  transform=ax_l.transAxes)
        ax_l.text(0.97, yc, f"{amt:,.0f} UZS ({pct}%)",
                  ha='right', va='center', fontsize=8, color=FG,
                  transform=ax_l.transAxes)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor=BG)
    buf.seek(0)
    plt.close(fig)
    return buf


# ===================== AI MASLAHAT =====================

async def get_ai_financial_advice(transactions: list, period_name: str, user_name: str) -> str:
    if not transactions:
        return None

    income   = sum(float(t["amount"]) for t in transactions if t["type"] == "income")
    expenses = sum(float(t["amount"]) for t in transactions if t["type"] == "expense")
    balance  = income - expenses

    cat_stats = {}
    for t in transactions:
        if t["type"] == "expense":
            cat = t.get("category", "Boshqa")
            cat_stats[cat] = cat_stats.get(cat, 0) + float(t["amount"])

    cat_lines = "\n".join(
        f"  - {cat}: {format_money(amt)} ({int(amt / expenses * 100) if expenses else 0}%)"
        for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1])
    ) if cat_stats else "  - Xarajatlar yo'q"

    exp_count = sum(1 for t in transactions if t["type"] == "expense")
    inc_count = len(transactions) - exp_count

    prompt = (
        f"Sen moliyaviy maslahatchi assistantsan. "
        f"Foydalanuvchining quyidagi moliyaviy ma'lumotlarini tahlil qilib, o'zbek tilida foydali maslahat ber.\n\n"
        f"Foydalanuvchi: {user_name}\n"
        f"Davr: {period_name}\n\n"
        f"MOLIYAVIY KO'RSATKICHLAR:\n"
        f"- Jami daromad: {format_money(income)} ({inc_count} ta)\n"
        f"- Jami xarajat: {format_money(expenses)} ({exp_count} ta)\n"
        f"- Sof balans: {format_money(balance)}\n\n"
        f"XARAJATLAR KATEGORIYALAR BO'YICHA:\n{cat_lines}\n\n"
        f"VAZIFANG:\n"
        f"1. Xarajat tuzilmasini qisqacha tahlil qil\n"
        f"2. Haddan oshib ketgan yoki e'tibor talab qiladigan sohalarni ko'rsat\n"
        f"3. 2-3 ta konkret va amaliy tejash yoki moliyaviy maslahat ber\n"
        f"4. Ijobiy yutuqlarni ham qayd et\n"
        f"5. O'zbek tilida, 200-250 so'z, oddiy va iliq uslubda yoz"
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 550,
                },
            )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
        logger.warning(f"AI maslahat API xato: {resp.status_code}")
    except Exception as e:
        logger.warning(f"AI maslahat exception: {e}")
    return None


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
         InlineKeyboardButton("📝 Oxirgi amaliyotlar", callback_data="recent")],
        [InlineKeyboardButton("📅 Sana oralig'i hisoboti", callback_data="date_range_report")],
        [InlineKeyboardButton("🤖 AI Maslahat", callback_data="ai_advice"),
         InlineKeyboardButton("📈 Oylik AI Xulosa", callback_data="ai_monthly")],
        [InlineKeyboardButton("💸 Qarzlar", callback_data="debts"),
         InlineKeyboardButton("💳 Balanslar", callback_data="balances")],
        [InlineKeyboardButton("🗑️ Tozalash", callback_data="clear_month"),
         InlineKeyboardButton("📖 Qo'llanma", callback_data="guide")],
        [InlineKeyboardButton("🌐 Web-kabinet", callback_data="web_cabinet")],
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

def _build_calendar_grid(year: int, month: int, pfx: str, mark_date=None,
                          start_date=None, end_date=None) -> list:
    """Kalendar tugmalar qatorlarini yaratadi (umumiy yordamchi)."""
    weeks = cal_module.monthcalendar(year, month)
    month_name = MONTH_NAMES[month]

    prev_year  = year if month > 1 else year - 1
    prev_month = month - 1 if month > 1 else 12
    next_year  = year if month < 12 else year + 1
    next_month = month + 1 if month < 12 else 1

    buttons = []
    buttons.append([
        InlineKeyboardButton("◀️", callback_data=f"{pfx}p_{prev_year}_{prev_month}"),
        InlineKeyboardButton(f"{month_name} {year}", callback_data=f"{pfx}x"),
        InlineKeyboardButton("▶️", callback_data=f"{pfx}n_{next_year}_{next_month}"),
    ])
    buttons.append([
        InlineKeyboardButton(d, callback_data=f"{pfx}x")
        for d in ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]
    ])
    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data=f"{pfx}x"))
            else:
                cur = date(year, month, day)
                label = str(day)
                if mark_date and cur == mark_date:
                    label = f"✔{day}"
                elif start_date and cur == start_date:
                    label = f"[{day}"
                elif end_date and cur == end_date:
                    label = f"{day}]"
                elif start_date and end_date and start_date < cur < end_date:
                    label = f"·{day}·"
                row.append(InlineKeyboardButton(label, callback_data=f"{pfx}d_{year}_{month}_{day}"))
        buttons.append(row)
    return buttons


def generate_calendar_keyboard(year: int, month: int, start_date=None, end_date=None):
    buttons = _build_calendar_grid(year, month, "cal_", start_date=start_date, end_date=end_date)
    if start_date and end_date:
        buttons.append([
            InlineKeyboardButton("✅ Tasdiqlash", callback_data="cal_confirm"),
            InlineKeyboardButton("🔄 Qayta tanlash", callback_data="date_range_report"),
        ])
    else:
        buttons.append([InlineKeyboardButton("❌ Bekor", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)


def generate_debt_date_keyboard(year: int, month: int, selected: date = None):
    buttons = _build_calendar_grid(year, month, "dbt_", mark_date=selected)
    if selected:
        buttons.append([
            InlineKeyboardButton(
                f"✅ {selected.strftime('%d.%m.%Y')} — Tasdiqlash",
                callback_data=f"dbt_confirm_{year}_{month}"
            ),
        ])
    buttons.append([
        InlineKeyboardButton("⏭️ Sana yo'q", callback_data="debt_skip_date"),
        InlineKeyboardButton("❌ Bekor", callback_data="back_main"),
    ])
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

# ----- Tranzaksiya tahrirlash klaviaturalari (YANGI) -----

def tx_confirm_keyboard(tx_id):
    """Tranzaksiya saqlangach chiqadigan tugmalar (tahrir/o'chir)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"txedit:{tx_id}"),
         InlineKeyboardButton("🗑 O'chirish", callback_data=f"txdel:{tx_id}")],
        [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
         InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
        [InlineKeyboardButton("📊 Statistika", callback_data="stats"),
         InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
    ])

def tx_edit_keyboard(tx_id):
    """Nimani tahrirlash menyusi."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Summa", callback_data=f"txamt:{tx_id}")],
        [InlineKeyboardButton("🏷 Kategoriya", callback_data=f"txcat:{tx_id}")],
        [InlineKeyboardButton("🔄 Tur (daromad/xarajat)", callback_data=f"txtype:{tx_id}")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data=f"txback:{tx_id}")],
    ])

async def render_tx_card(telegram_id, tx_id):
    """Tranzaksiya kartasini (matn + tugmalar) tayyorlaydi."""
    tx = await get_transaction(telegram_id, tx_id)
    if not tx:
        return None, None
    emoji  = "📥" if tx["type"] == "income" else "📤"
    type_t = "Daromad" if tx["type"] == "income" else "Xarajat"
    note_t = f"\n📝 Izoh: {tx['note']}" if tx.get("note") else ""
    text = (
        f"✅ <b>{type_t}</b>\n\n"
        f"{emoji} Miqdor: <b>{format_money(float(tx['amount']))}</b>\n"
        f"📁 Kategoriya: {tx['category']}{note_t}"
    )
    return text, tx_confirm_keyboard(tx_id)

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
        "/start — Bosh menyu\n/help — Yordam\n/oxirgi — Oxirgi amaliyotlar (tahrirlash)\n\n"
        "➕ Daromad/Xarajat kiritish\n"
        "🎤 Ovoz orqali kiritish (bir nechta amaliyot ham)\n"
        "📸 Chek rasm orqali kiritish\n"
        "✏️ Har bir amaliyotni tahrirlash/o'chirish\n"
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

async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/oxirgi — oxirgi amaliyotlar ro'yxati (tahrirlash uchun)."""
    user_id = update.effective_user.id
    if not await is_user_premium(user_id):
        await show_payment_screen(update, context)
        return
    await _show_recent(user_id, reply_fn=update.message.reply_text)

async def mcp_token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mcp_token — 24 soatlik MCP Bearer token generatsiya qiladi."""
    user_id = update.effective_user.id
    if not await is_user_premium(user_id):
        await update.message.reply_text(
            "❌ Bu funksiya faqat premium foydalanuvchilar uchun.",
            parse_mode="HTML",
        )
        return
    token = await create_mcp_token(user_id)
    await update.message.reply_text(
        "🔑 <b>MCP API Token</b>\n\n"
        f"<code>{token}</code>\n\n"
        "⏰ <b>24 soat</b> davomida amal qiladi.\n"
        "🔒 Bu tokenni hech kim bilan ulashmang!\n\n"
        "<b>Ishlatish:</b>\n"
        "<code>Authorization: Bearer &lt;token&gt;</code>\n\n"
        f"📋 Manifest: <code>{WEBHOOK_URL}/.well-known/mcp.json</code>",
        parse_mode="HTML",
    )

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

    # ---------- TRANZAKSIYANI TAHRIRLASH ----------
    if data.startswith("txedit:"):
        tx_id = int(data.split(":")[1])
        tx = await get_transaction(user_id, tx_id)
        if not tx:
            await query.edit_message_text("❌ Amaliyot topilmadi (o'chirilgan).")
            return
        emoji  = "📥" if tx["type"] == "income" else "📤"
        type_t = "Daromad" if tx["type"] == "income" else "Xarajat"
        await query.edit_message_text(
            f"✏️ <b>Tahrirlash</b>\n\n"
            f"{emoji} {type_t}: <b>{format_money(float(tx['amount']))}</b>\n"
            f"🏷 {tx['category']}\n\nNimani o'zgartiramiz?",
            parse_mode="HTML", reply_markup=tx_edit_keyboard(tx_id))

    elif data.startswith("txback:"):
        tx_id = int(data.split(":")[1])
        text, kb = await render_tx_card(user_id, tx_id)
        if text:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await query.edit_message_text("❌ Amaliyot topilmadi.")

    elif data.startswith("txdel:"):
        tx_id = int(data.split(":")[1])
        ok = await delete_transaction(user_id, tx_id)
        await query.edit_message_text(
            "🗑 <b>Amaliyot o'chirildi, balans tiklandi.</b>" if ok else "❌ Amaliyot topilmadi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📝 Oxirgi amaliyotlar", callback_data="recent")],
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")]]))

    elif data.startswith("txamt:"):
        tx_id = int(data.split(":")[1])
        context.user_data["awaiting_edit_amount"] = True
        context.user_data["edit_tx_id"] = tx_id
        await query.edit_message_text(
            "💰 Yangi summani kiriting (faqat raqam):\n<i>Masalan: 50000</i>",
            parse_mode="HTML")

    elif data.startswith("txtype:"):
        tx_id = int(data.split(":")[1])
        tx = await get_transaction(user_id, tx_id)
        if not tx:
            await query.edit_message_text("❌ Amaliyot topilmadi.")
            return
        if tx["type"] == "expense":
            new_type, new_cat = "income", "📦 Boshqa daromad"
        else:
            new_type, new_cat = "expense", "📦 Boshqa"
        await update_transaction(user_id, tx_id, new_type=new_type, new_category=new_cat)
        text, kb = await render_tx_card(user_id, tx_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

    elif data.startswith("txcat:"):
        tx_id = int(data.split(":")[1])
        tx = await get_transaction(user_id, tx_id)
        if not tx:
            await query.edit_message_text("❌ Amaliyot topilmadi.")
            return
        cats = INCOME_CATEGORIES if tx["type"] == "income" else EXPENSE_CATEGORIES
        buttons, row = [], []
        for i, c in enumerate(cats):
            row.append(InlineKeyboardButton(c, callback_data=f"txsetcat:{tx_id}:{i}"))
            if len(row) == 2:
                buttons.append(row); row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=f"txedit:{tx_id}")])
        await query.edit_message_text(
            "🏷 <b>Yangi kategoriyani tanlang:</b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("txsetcat:"):
        _, tx_id_s, idx_s = data.split(":")
        tx_id = int(tx_id_s)
        tx = await get_transaction(user_id, tx_id)
        if not tx:
            await query.edit_message_text("❌ Amaliyot topilmadi.")
            return
        cats = INCOME_CATEGORIES if tx["type"] == "income" else EXPENSE_CATEGORIES
        await update_transaction(user_id, tx_id, new_category=cats[int(idx_s)])
        text, kb = await render_tx_card(user_id, tx_id)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "recent":
        await _show_recent(user_id, query=query)

    # ---------- ASOSIY HANDLERLAR ----------
    elif data == "add_income":
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
                f"💵 {format_money(stats['balance'])}\n\n"
                f"<i>Tahrirlash uchun 📝 Oxirgi amaliyotlar tugmasini bosing</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 Oxirgi amaliyotlar", callback_data="recent")],
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
            tx_id = await add_transaction(
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
                reply_markup=tx_confirm_keyboard(tx_id)
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

        month_str = datetime.now().strftime("%B %Y")
        stats_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 PDF yuklab olish", callback_data="stats_pdf")],
            [InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")]
        ])

        chart_buf = generate_donut_chart(
            cat_stats, stats['expenses'], stats['income'], month_str
        )
        if chart_buf:
            caption = (
                f"📊 <b>Statistika — {month_str}</b>\n\n"
                f"📥 Daromad: <b>{format_money(stats['income'])}</b>\n"
                f"📤 Xarajat: <b>{format_money(stats['expenses'])}</b>\n"
                f"💵 Balans:  <b>{format_money(stats['balance'])}</b>"
            )
            if budget > 0:
                used = int(stats['expenses'] / budget * 100) if budget else 0
                rem  = budget - stats['expenses']
                caption += (
                    f"\n\n🎯 Budget: {format_money(budget)} ({used}% sarflandi)"
                )
                if rem < 0:
                    caption += f"\n⚠️ {format_money(abs(rem))} oshib ketdi!"
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_photo(
                chat_id=user_id,
                photo=chart_buf,
                caption=caption,
                parse_mode="HTML",
                reply_markup=stats_kb,
            )
        else:
            msg = f"📊 <b>Statistika — {month_str}</b>\n\n"
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
            await safe_edit(msg, parse_mode="HTML", reply_markup=stats_kb)

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

    # ---------- AI MASLAHAT ----------
    elif data == "ai_advice":
        await query.edit_message_text(
            "🤖 <b>AI tahlil qilinmoqda...</b>\n\n<i>Bir daqiqa sabr qiling...</i>",
            parse_mode="HTML"
        )
        txns      = await get_month_transactions(user_id)
        period    = datetime.now().strftime("%B %Y")
        user_name = query.from_user.full_name

        if not txns:
            await query.edit_message_text(
                f"📊 <b>{period}</b>\n\nBu oy hali tranzaksiyalar yo'q.\n"
                f"Daromad va xarajatlaringizni kiritiing, keyin AI tahlil qila oladi.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")]])
            )
            return

        advice = await get_ai_financial_advice(txns, period, user_name)
        income   = sum(float(t["amount"]) for t in txns if t["type"] == "income")
        expenses = sum(float(t["amount"]) for t in txns if t["type"] == "expense")

        msg = (
            f"🤖 <b>AI Moliyaviy Maslahat — {period}</b>\n\n"
            f"📥 Daromad: <b>{format_money(income)}</b>  |  "
            f"📤 Xarajat: <b>{format_money(expenses)}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{advice or 'AI vaqtincha javob bermadi. Keyinroq urinib ko`ring.'}"
        )
        await query.edit_message_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yangilash", callback_data="ai_advice")],
                [InlineKeyboardButton("📈 Oylik AI Xulosa", callback_data="ai_monthly"),
                 InlineKeyboardButton("🏠 Menyu", callback_data="back_main")],
            ])
        )

    elif data == "ai_monthly":
        await query.edit_message_text(
            "📈 <b>Oylik AI xulosa tayyorlanmoqda...</b>\n\n<i>Bir daqiqa sabr qiling...</i>",
            parse_mode="HTML"
        )
        now = datetime.now()
        prev_month = now.month - 1 if now.month > 1 else 12
        prev_year  = now.year if now.month > 1 else now.year - 1
        txns_cur   = await get_month_transactions(user_id)
        txns_prev  = await get_transactions_by_month(user_id, prev_year, prev_month)
        user_name  = query.from_user.full_name
        cur_period  = now.strftime("%B %Y")
        prev_period = f"{MONTH_NAMES[prev_month]} {prev_year}"

        if not txns_cur and not txns_prev:
            await query.edit_message_text(
                "📊 Tahlil uchun yetarli ma'lumot yo'q.\nAvval daromad va xarajatlaringizni kiriting.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")]])
            )
            return

        target_txns   = txns_cur if txns_cur else txns_prev
        target_period = cur_period if txns_cur else prev_period

        advice = await get_ai_financial_advice(target_txns, target_period, user_name)

        income   = sum(float(t["amount"]) for t in target_txns if t["type"] == "income")
        expenses = sum(float(t["amount"]) for t in target_txns if t["type"] == "expense")

        # Oldingi oy bilan taqqoslash
        compare_text = ""
        if txns_cur and txns_prev:
            prev_exp = sum(float(t["amount"]) for t in txns_prev if t["type"] == "expense")
            diff     = expenses - prev_exp
            arrow    = "📈" if diff > 0 else "📉"
            compare_text = (
                f"\n{arrow} O'tgan oy ({prev_period}) xarajat: <b>{format_money(prev_exp)}</b>\n"
                f"Farq: <b>{'+'if diff>0 else ''}{format_money(diff)}</b>\n"
            )

        msg = (
            f"📈 <b>Oylik AI Xulosa — {target_period}</b>\n\n"
            f"📥 Daromad: <b>{format_money(income)}</b>\n"
            f"📤 Xarajat: <b>{format_money(expenses)}</b>"
            f"{compare_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{advice or 'AI vaqtincha javob bermadi. Keyinroq urinib ko`ring.'}"
        )
        await query.edit_message_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 Joriy oy maslahat", callback_data="ai_advice")],
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

    # ---------- SANA ORALIG'I HISOBOTI ----------
    elif data == "date_range_report":
        for k in ("date_range_start", "date_range_end", "cal_year", "cal_month"):
            context.user_data.pop(k, None)
        now = datetime.now()
        year, month = now.year, now.month
        context.user_data["cal_year"] = year
        context.user_data["cal_month"] = month
        await query.edit_message_text(
            "📅 <b>Sana oralig'i hisoboti</b>\n\n"
            "Boshlanish sanasini tanlang 👇",
            parse_mode="HTML",
            reply_markup=generate_calendar_keyboard(year, month)
        )

    elif data == "cal_x":
        await query.answer()

    elif data.startswith("cal_p_") or data.startswith("cal_n_"):
        parts = data.split("_")
        year, month = int(parts[2]), int(parts[3])
        context.user_data["cal_year"] = year
        context.user_data["cal_month"] = month
        start = context.user_data.get("date_range_start")
        end   = context.user_data.get("date_range_end")
        if start and end:
            header = (
                f"📅 <b>Sana oralig'i tanlandi:</b>\n"
                f"▶️ Boshlanish: <b>{start.strftime('%d.%m.%Y')}</b>\n"
                f"⏹ Tugash: <b>{end.strftime('%d.%m.%Y')}</b>\n\n"
                f"✅ Tasdiqlang yoki boshqa oraliq tanlang"
            )
        elif start:
            header = (
                f"📅 <b>Tugash sanasini tanlang:</b>\n"
                f"▶️ Boshlanish: <b>{start.strftime('%d.%m.%Y')}</b>"
            )
        else:
            header = "📅 <b>Boshlanish sanasini tanlang:</b>"
        await query.edit_message_text(
            header, parse_mode="HTML",
            reply_markup=generate_calendar_keyboard(year, month, start, end)
        )

    elif data.startswith("cal_d_"):
        parts = data.split("_")
        year, month, day = int(parts[2]), int(parts[3]), int(parts[4])
        selected = date(year, month, day)
        start = context.user_data.get("date_range_start")
        end   = context.user_data.get("date_range_end")
        cal_year  = context.user_data.get("cal_year", year)
        cal_month = context.user_data.get("cal_month", month)

        if not start:
            context.user_data["date_range_start"] = selected
            start = selected
            header = (
                f"📅 <b>Tugash sanasini tanlang:</b>\n"
                f"▶️ Boshlanish: <b>{start.strftime('%d.%m.%Y')}</b>"
            )
        elif not end:
            if selected == start:
                await query.answer("Boshqa kun tanlang")
                return
            if selected < start:
                selected, start = start, selected
                context.user_data["date_range_start"] = start
            context.user_data["date_range_end"] = selected
            end = selected
            header = (
                f"📅 <b>Sana oralig'i tanlandi:</b>\n"
                f"▶️ Boshlanish: <b>{start.strftime('%d.%m.%Y')}</b>\n"
                f"⏹ Tugash: <b>{end.strftime('%d.%m.%Y')}</b>\n\n"
                f"✅ Tasdiqlang yoki boshqa oraliq tanlang"
            )
        else:
            context.user_data["date_range_start"] = selected
            context.user_data.pop("date_range_end", None)
            start, end = selected, None
            header = (
                f"📅 <b>Tugash sanasini tanlang:</b>\n"
                f"▶️ Boshlanish: <b>{start.strftime('%d.%m.%Y')}</b>"
            )

        await query.edit_message_text(
            header, parse_mode="HTML",
            reply_markup=generate_calendar_keyboard(cal_year, cal_month, start, end)
        )

    elif data == "cal_confirm":
        start = context.user_data.pop("date_range_start", None)
        end   = context.user_data.pop("date_range_end", None)
        for k in ("cal_year", "cal_month"):
            context.user_data.pop(k, None)

        if not start or not end:
            await query.answer("❌ Ikkala sana ham tanlanmagan!")
            return

        await query.edit_message_text(
            f"⏳ <b>{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}</b> hisobot tayyorlanmoqda...",
            parse_mode="HTML"
        )

        txns = await get_transactions_by_date_range(user_id, start, end)
        start_str = start.strftime("%d.%m.%Y")
        end_str   = end.strftime("%d.%m.%Y")

        if not txns:
            await query.edit_message_text(
                f"📋 <b>{start_str} — {end_str}</b>\n\n"
                f"Bu sana oralig'ida tranzaksiyalar topilmadi.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📅 Yangi oraliq", callback_data="date_range_report"),
                    InlineKeyboardButton("🏠 Menyu", callback_data="back_main")
                ]])
            )
            return

        income   = sum(float(t["amount"]) for t in txns if t["type"] == "income")
        expenses = sum(float(t["amount"]) for t in txns if t["type"] == "expense")
        balance  = income - expenses

        cat_stats = {}
        for t in txns:
            if t["type"] == "expense":
                cat = t.get("category", "Boshqa")
                cat_stats[cat] = cat_stats.get(cat, 0) + float(t["amount"])

        range_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📅 Yangi oraliq", callback_data="date_range_report"),
            InlineKeyboardButton("🏠 Menyu", callback_data="back_main")
        ]])

        txn_lines = ""
        for t in txns[:20]:
            emoji    = "📥" if t["type"] == "income" else "📤"
            date_str = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else str(t["date"])[:10]
            bal      = f" | 💳 {t['balance_name']}" if t.get("balance_name") else ""
            note     = f" — {t['note']}" if t.get("note") else ""
            txn_lines += f"{emoji} <b>{format_money(float(t['amount']))}</b> · {t.get('category','Boshqa')}{bal} | {date_str}{note}\n"
        if len(txns) > 20:
            txn_lines += f"\n<i>...va yana {len(txns) - 20} ta amaliyot</i>\n"

        chart_buf = generate_donut_chart(
            cat_stats, expenses, income, f"{start_str} — {end_str}"
        )
        if chart_buf:
            caption = (
                f"📅 <b>Hisobot: {start_str} — {end_str}</b>\n\n"
                f"📥 Daromad: <b>{format_money(income)}</b>\n"
                f"📤 Xarajat: <b>{format_money(expenses)}</b>\n"
                f"💵 Balans:  <b>{format_money(balance)}</b>"
            )
            if txn_lines:
                caption += f"\n\n📋 <b>Amaliyotlar ({len(txns)} ta):</b>\n" + txn_lines
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_photo(
                chat_id=user_id,
                photo=chart_buf,
                caption=caption,
                parse_mode="HTML",
                reply_markup=range_kb,
            )
        else:
            msg = (
                f"📅 <b>Hisobot: {start_str} — {end_str}</b>\n\n"
                "┌─────────────────────────┐\n"
                f"│ 📥 Daromad : {format_money(income):>12} │\n"
                f"│ 📤 Xarajat : {format_money(expenses):>12} │\n"
                f"│ 💵 Balans  : {format_money(balance):>12} │\n"
                "└─────────────────────────┘\n"
            )
            if cat_stats:
                msg += f"\n🏆 <b>Xarajatlar bo'yicha:</b>\n"
                msg += "─" * 28 + "\n"
                for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1]):
                    pct = int(amt / expenses * 100) if expenses else 0
                    msg += f"  {cat}: <b>{format_money(amt)}</b> ({pct}%)\n"
            msg += f"\n📋 <b>Amaliyotlar ({len(txns)} ta):</b>\n"
            msg += "─" * 28 + "\n" + txn_lines
            await query.edit_message_text(
                msg, parse_mode="HTML", reply_markup=range_kb
            )

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
        debts = await get_debts(user_id)
        debt = next((d for d in debts if d["id"] == debt_id), None)
        if not debt:
            await query.answer("❌ Qarz topilmadi")
            return
        balances = await get_balances(user_id)
        direction = debt["direction"]
        amount    = float(debt["amount"])
        person    = debt["person_name"]
        emoji     = "🔴" if direction == "gave" else "🟢"
        action    = "qaytaradi → qo'shamiz" if direction == "gave" else "qaytaramiz → yechilamiz"

        msg = (
            f"{emoji} <b>{person}</b> — <b>{format_money(amount)}</b>\n\n"
            f"💳 Qaysi balansga <b>{action}</b>?"
        )
        buttons = []
        for b in balances:
            type_emoji = BALANCE_TYPES.get(b["type"], "📦").split()[0]
            label = f"{type_emoji} {b['name']} — {format_money(float(b['amount']))}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"debt_ret_bal_{debt_id}_{b['id']}")])
        buttons.append([InlineKeyboardButton("⏭️ Balanssiz belgilash", callback_data=f"debt_ret_no_bal_{debt_id}")])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="debt_paid_list")])
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("debt_ret_bal_"):
        parts     = data.split("_")
        debt_id   = int(parts[3])
        bal_id    = int(parts[4])
        await mark_debt_paid(debt_id, return_balance_id=bal_id)
        async with db_pool.acquire() as conn:
            bal = await conn.fetchrow("SELECT name, amount FROM balances WHERE id = $1", bal_id)
        bal_text = f"\n💳 Balans: <b>{bal['name']}</b> — {format_money(float(bal['amount']))}" if bal else ""
        await query.edit_message_text(
            f"✅ <b>Qarz to'landi!</b>{bal_text}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Qarzlar", callback_data="debts")]]))

    elif data.startswith("debt_ret_no_bal_"):
        debt_id = int(data.split("_")[4])
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

    elif data == "dbt_x":
        await query.answer()

    elif data.startswith("dbt_p_") or data.startswith("dbt_n_"):
        parts = data.split("_")
        year, month = int(parts[2]), int(parts[3])
        context.user_data["dbt_cal_year"]  = year
        context.user_data["dbt_cal_month"] = month
        selected = context.user_data.get("dbt_selected_date")
        amount   = context.user_data.get("debt_amount", 0)
        person   = context.user_data.get("debt_person", "")
        await query.edit_message_text(
            f"💰 Miqdor: <b>{format_money(amount)}</b>  👤 <b>{person}</b>\n\n"
            f"📅 Qaytarish sanasini tanlang:",
            parse_mode="HTML",
            reply_markup=generate_debt_date_keyboard(year, month, selected)
        )

    elif data.startswith("dbt_d_"):
        parts = data.split("_")
        year, month, day = int(parts[2]), int(parts[3]), int(parts[4])
        selected = date(year, month, day)
        context.user_data["dbt_selected_date"] = selected
        cal_year  = context.user_data.get("dbt_cal_year", year)
        cal_month = context.user_data.get("dbt_cal_month", month)
        amount = context.user_data.get("debt_amount", 0)
        person = context.user_data.get("debt_person", "")
        await query.edit_message_text(
            f"💰 Miqdor: <b>{format_money(amount)}</b>  👤 <b>{person}</b>\n\n"
            f"📅 Tanlangan sana: <b>{selected.strftime('%d.%m.%Y')}</b>\n\n"
            f"Tasdiqlash yoki boshqa sana tanlang:",
            parse_mode="HTML",
            reply_markup=generate_debt_date_keyboard(cal_year, cal_month, selected)
        )

    elif data.startswith("dbt_confirm_"):
        selected = context.user_data.pop("dbt_selected_date", None)
        for k in ("dbt_cal_year", "dbt_cal_month"):
            context.user_data.pop(k, None)
        if not selected:
            await query.answer("❌ Sana tanlanmagan!")
            return
        await _ask_debt_balance(user_id, context, due_date=selected, via_query=query)

    elif data == "debt_skip_date":
        for k in ("dbt_selected_date", "dbt_cal_year", "dbt_cal_month"):
            context.user_data.pop(k, None)
        await _ask_debt_balance(user_id, context, due_date=None, via_query=query)

    elif data == "debt_skip_bal":
        await _save_debt(user_id, context, balance_id=None, via_query=query)

    elif data.startswith("debt_sel_bal_"):
        balance_id = int(data.split("_")[3])
        await _save_debt(user_id, context, balance_id=balance_id, via_query=query)

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

    elif data == "web_cabinet":
        premium = await is_user_premium(user_id)
        if not premium and user_id != ADMIN_ID:
            await safe_edit(
                "🌐 <b>Web Kabinet</b>\n\n"
                "Bu funksiya faqat <b>premium</b> foydalanuvchilar uchun.\n\n"
                "Premium obuna orqali moliyangizni web saytda keng ko'rinishda kuzating!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💎 Premium olish", callback_data="premium")],
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
                ])
            )
            return
        if not DASHBOARD_URL:
            await safe_edit(
                "🌐 <b>Web Kabinet</b>\n\n"
                "⚠️ Dashboard hali sozlanmagan.\n\n"
                "Admin sozlamalarida <b>DASHBOARD_URL</b> ni kiriting.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
                ])
            )
            return
        try:
            code = await create_login_code(user_id)
        except Exception as e:
            logger.error(f"create_login_code xatolik: {e}")
            await safe_edit(
                "🌐 <b>Web Kabinet</b>\n\n"
                "❌ Kod yaratishda xatolik yuz berdi. Iltimos qaytadan urinib ko'ring.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Qayta urinish", callback_data="web_cabinet")],
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
                ])
            )
            return
        await safe_edit(
            f"🌐 <b>Web Kabinet</b>\n\n"
            f"Quyidagi ma'lumotlar bilan kiring:\n\n"
            f"🔗 <b>Havola:</b> {DASHBOARD_URL}\n\n"
            f"🆔 <b>Telegram ID:</b> <code>{user_id}</code>\n"
            f"🔑 <b>Kirish kodi:</b> <code>{code}</code>\n\n"
            f"⏱ Kod <b>10 daqiqa</b> amal qiladi va faqat bir marta ishlatiladi.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yangi kod", callback_data="web_cabinet")],
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
            ])
        )

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
        context.user_data.get("awaiting_balance_name"),
        context.user_data.get("awaiting_balance_amount"),
        context.user_data.get("awaiting_balance_update"),
        context.user_data.get("awaiting_broadcast"),
        context.user_data.get("awaiting_receipt_amount"),
        context.user_data.get("awaiting_receipt_note"),
        context.user_data.get("awaiting_edit_amount"),
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
            now = datetime.now()
            context.user_data["dbt_cal_year"]  = now.year
            context.user_data["dbt_cal_month"] = now.month
            await update.message.reply_text(
                f"💰 Miqdor: <b>{format_money(amount)}</b>\n\n"
                f"📅 Qaytarish sanasini tanlang:",
                parse_mode="HTML",
                reply_markup=generate_debt_date_keyboard(now.year, now.month)
            )
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

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

    # ---------- TRANZAKSIYA SUMMASINI TAHRIRLASH (YANGI) ----------
    elif context.user_data.get("awaiting_edit_amount"):
        try:
            amount = float(text.replace(" ", "").replace(",", ""))
            if amount <= 0:
                raise ValueError
            tx_id = context.user_data.pop("edit_tx_id", None)
            context.user_data.pop("awaiting_edit_amount", None)
            await update_transaction(user_id, tx_id, new_amount=amount)
            text_card, kb = await render_tx_card(user_id, tx_id)
            if text_card:
                await update.message.reply_text(text_card, parse_mode="HTML", reply_markup=kb)
            else:
                await update.message.reply_text("❌ Amaliyot topilmadi.")
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

async def _ask_debt_balance(user_id, context, due_date, reply_fn=None, via_query=None):
    """Qarzni saqlashdan oldin qaysi balansdan yechishni so'raydi."""
    context.user_data["debt_due_date"] = due_date
    balances = await get_balances(user_id)
    direction = context.user_data.get("debt_direction", "gave")
    amount    = context.user_data.get("debt_amount", 0)
    person    = context.user_data.get("debt_person", "")
    emoji     = "🔴" if direction == "gave" else "🟢"
    action    = "yechiladi" if direction == "gave" else "qo'shiladi"

    msg = (
        f"{emoji} <b>{person}</b> — <b>{format_money(amount)}</b>\n\n"
        f"💳 Qaysi balansdan <b>{action}</b>?"
    )
    buttons = []
    for b in balances:
        type_emoji = BALANCE_TYPES.get(b["type"], "📦").split()[0]
        label = f"{type_emoji} {b['name']} — {format_money(float(b['amount']))}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"debt_sel_bal_{b['id']}")])
    buttons.append([InlineKeyboardButton("⏭️ Balanssiz saqlash", callback_data="debt_skip_bal")])
    buttons.append([InlineKeyboardButton("❌ Bekor", callback_data="back_main")])
    markup = InlineKeyboardMarkup(buttons)

    if via_query:
        await via_query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)
    elif reply_fn:
        await reply_fn(msg, parse_mode="HTML", reply_markup=markup)

async def _save_debt(user_id, context, balance_id=None, reply_fn=None, via_query=None):
    person    = context.user_data.get("debt_person", "")
    amount    = context.user_data.get("debt_amount", 0)
    direction = context.user_data.get("debt_direction", "gave")
    due_date  = context.user_data.get("debt_due_date")

    for k in ("debt_person", "debt_amount", "debt_direction", "debt_due_date",
              "awaiting_debt_date"):
        context.user_data.pop(k, None)

    await add_debt(user_id, person, amount, direction, due_date, balance_id=balance_id)

    direction_text = "bergan" if direction == "gave" else "olgan"
    due_text = f"\n📅 Qaytarish: {due_date.strftime('%d.%m.%Y')}" if due_date else ""
    emoji = "🔴" if direction == "gave" else "🟢"

    bal_text = ""
    if balance_id:
        async with db_pool.acquire() as conn:
            bal = await conn.fetchrow("SELECT name, amount FROM balances WHERE id = $1", balance_id)
            if bal:
                action = "yechildi" if direction == "gave" else "qo'shildi"
                bal_text = f"\n💳 Balans: <b>{bal['name']}</b> — {format_money(float(bal['amount']))} ({action})"

    msg = (
        f"✅ <b>Qarz saqlandi!</b>\n\n"
        f"{emoji} Men <b>{direction_text}</b>\n"
        f"👤 Ism: <b>{person}</b>\n"
        f"💰 Miqdor: <b>{format_money(amount)}</b>{due_text}{bal_text}"
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

    tx_id = await add_transaction(user_id, txn_type, amount, category, note, balance_id)
    txns   = await get_month_transactions(user_id)
    stats  = calc_stats(txns)
    budget = await get_budget(user_id)

    emoji  = "📥" if txn_type == "income" else "📤"
    note_t = f"\n📝 Izoh: {note}" if note else ""

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

    # Tahrir/o'chirish tugmalari bilan
    markup = tx_confirm_keyboard(tx_id)

    if via_query:
        await via_query.edit_message_text(msg, parse_mode="HTML", reply_markup=markup)
    elif reply_fn:
        await reply_fn(msg, parse_mode="HTML", reply_markup=markup)

async def _show_recent(telegram_id, reply_fn=None, query=None):
    """Oxirgi amaliyotlar ro'yxati — har biriga tahrirlash tugmasi."""
    txns = await get_recent_transactions(telegram_id)
    if not txns:
        text = "📋 Hali amaliyot yo'q."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="back_main")]])
    else:
        text = "📝 <b>Oxirgi amaliyotlar</b>\nTahrirlash uchun tanlang:"
        buttons = []
        for t in txns:
            emoji = "📥" if t["type"] == "income" else "📤"
            d = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else ""
            label = f"{emoji} {format_money(float(t['amount']))} — {t['category']} ({d})"
            buttons.append([InlineKeyboardButton(label[:60], callback_data=f"txedit:{t['id']}")])
        buttons.append([InlineKeyboardButton("🏠 Menyu", callback_data="back_main")])
        kb = InlineKeyboardMarkup(buttons)
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    elif reply_fn:
        await reply_fn(text, parse_mode="HTML", reply_markup=kb)

# ===================== HAFTALIK PDF =====================

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

    elements.append(Paragraph("Haftalik Moliyaviy Hisobot", title_style))
    elements.append(Paragraph(
        f"Foydalanuvchi: {user_name} | "
        f"Davr: {week_start.strftime('%d.%m.%Y')} - {week_end.strftime('%d.%m.%Y')}",
        subtitle_style
    ))
    elements.append(Spacer(1, 0.3*cm))

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
            users = await conn.fetch("SELECT telegram_id, name FROM users")

        logger.info(f"📊 Haftalik hisobot: {len(users)} foydalanuvchiga yuboriladi")

        today = datetime.now(pytz.timezone("Asia/Tashkent")).date()
        last_monday = today - timedelta(days=7)
        last_sunday = today - timedelta(days=1)

        sent = 0
        failed = 0
        skipped = 0

        for user in users:
            user_id = user["telegram_id"]
            name = user["name"] or "Do'stim"

            premium = await is_user_premium(user_id)
            if not premium:
                skipped += 1
                continue

            async with db_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT type, amount, category, date
                    FROM transactions
                    WHERE telegram_id = $1
                      AND DATE(date AT TIME ZONE 'Asia/Tashkent') >= $2
                      AND DATE(date AT TIME ZONE 'Asia/Tashkent') <= $3
                """, user_id, last_monday, last_sunday)

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
                    cat = r["category"] or "Boshqa"
                    categories[cat] = categories.get(cat, 0) + amt
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
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
                         InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
                        [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
                    ])
                )

                # AI haftalik xulosa
                week_txns = [
                    {"type": r["type"], "amount": r["amount"],
                     "category": r.get("category", "Boshqa")}
                    for r in rows
                ]
                period_str = (
                    f"{last_monday.strftime('%d.%m')} – {last_sunday.strftime('%d.%m.%Y')}"
                )
                advice = await get_ai_financial_advice(week_txns, period_str, name)
                if advice:
                    await bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"🤖 <b>AI haftalik xulosa</b>\n\n"
                            f"📅 {period_str}\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"{advice}"
                        ),
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🤖 AI Maslahat", callback_data="ai_advice"),
                            InlineKeyboardButton("🏠 Menyu", callback_data="back_main"),
                        ]])
                    )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ PDF xato {user_id}: {e}")

            await asyncio.sleep(0.15)

        logger.info(
            f"✅ Haftalik hisobot: {sent} yuborildi | "
            f"❌ {failed} xato | ⏭️ {skipped} o'tkazildi"
        )

    except Exception as e:
        logger.error(f"❌ Haftalik hisobot xato: {e}")


# ===================== OYLIK AI XULOSA =====================

async def send_monthly_ai_summaries(bot):
    """Har oyning 1-sanasida o'tgan oyning AI xulosasini yuboradi."""
    try:
        now         = datetime.now(pytz.timezone("Asia/Tashkent"))
        prev_month  = now.month - 1 if now.month > 1 else 12
        prev_year   = now.year if now.month > 1 else now.year - 1
        period_str  = f"{MONTH_NAMES[prev_month]} {prev_year}"

        async with db_pool.acquire() as conn:
            users = await conn.fetch("SELECT telegram_id, name FROM users")

        sent = 0; failed = 0; skipped = 0

        for user in users:
            user_id   = user["telegram_id"]
            user_name = user["name"] or "Foydalanuvchi"

            try:
                premium = await is_user_premium(user_id)
                if not premium:
                    skipped += 1
                    continue

                txns = await get_transactions_by_month(user_id, prev_year, prev_month)
                if not txns:
                    skipped += 1
                    continue

                advice = await get_ai_financial_advice(txns, period_str, user_name)
                if not advice:
                    skipped += 1
                    continue

                income   = sum(float(t["amount"]) for t in txns if t["type"] == "income")
                expenses = sum(float(t["amount"]) for t in txns if t["type"] == "expense")

                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"📈 <b>Oylik AI Xulosa — {period_str}</b>\n\n"
                        f"📥 Daromad: <b>{format_money(income)}</b>\n"
                        f"📤 Xarajat: <b>{format_money(expenses)}</b>\n"
                        f"💵 Balans: <b>{format_money(income - expenses)}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{advice}"
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🤖 AI Maslahat", callback_data="ai_advice"),
                        InlineKeyboardButton("🏠 Menyu", callback_data="back_main"),
                    ]])
                )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ Oylik AI xulosa xato {user_id}: {e}")

            await asyncio.sleep(0.3)

        logger.info(
            f"✅ Oylik AI xulosa: {sent} yuborildi | "
            f"❌ {failed} xato | ⏭️ {skipped} o'tkazildi"
        )

    except Exception as e:
        logger.error(f"❌ Oylik AI xulosa xato: {e}")


# ===================== KUNLIK ESLATMA =====================

async def send_debt_reminders(bot):
    """Har kuni 9:00 (Toshkent) da qarz eslatmalarini yuboradi."""
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    d.telegram_id,
                    d.person_name,
                    d.amount,
                    d.direction,
                    d.due_date,
                    (d.due_date - CURRENT_DATE) AS days_left
                FROM debts d
                WHERE d.is_paid = FALSE
                  AND d.due_date IS NOT NULL
                  AND (
                      (d.due_date - CURRENT_DATE) IN (30, 14, 7, 3, 2, 1, 0)
                      OR d.due_date < CURRENT_DATE
                  )
                ORDER BY d.telegram_id, d.due_date ASC
            """)

        if not rows:
            logger.info("📭 Qarz eslatmasi: bugun eslatma yuborish kerak bo'lgan qarz yo'q")
            return

        user_debts = {}
        for r in rows:
            uid = r["telegram_id"]
            days = r["days_left"]
            if uid not in user_debts:
                user_debts[uid] = {
                    "overdue": [],
                    "today": [],
                    "urgent": [],
                    "soon": [],
                    "future": [],
                }
            if days < 0:
                user_debts[uid]["overdue"].append(r)
            elif days == 0:
                user_debts[uid]["today"].append(r)
            elif days <= 3:
                user_debts[uid]["urgent"].append(r)
            elif days <= 14:
                user_debts[uid]["soon"].append(r)
            else:
                user_debts[uid]["future"].append(r)

        logger.info(f"💸 Qarz eslatmasi: {len(user_debts)} foydalanuvchiga yuboriladi")

        sent = 0
        failed = 0
        for uid, debts in user_debts.items():
            premium = await is_user_premium(uid)
            if not premium:
                continue

            msg = "💸 <b>Qarz eslatmasi!</b>\n\n"

            if debts["overdue"]:
                msg += "⚠️ <b>KECHIKKAN!</b>\n"
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
                for d in debts["overdue"]:
                    days_late = abs(d["days_left"])
                    action = "qaytarishingiz" if d["direction"] == "took" else "olishingiz"
                    msg += (
                        f"⛔️ <b>{days_late} kun kechikdi!</b>\n"
                        f"👤 {d['person_name']} — {format_money(float(d['amount']))}\n"
                        f"   <i>{action} kerak edi: {d['due_date'].strftime('%d.%m.%Y')}</i>\n\n"
                    )

            if debts["today"]:
                msg += "🚨 <b>BUGUN qaytarish kerak!</b>\n"
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
                for d in debts["today"]:
                    emoji = "🟢" if d["direction"] == "took" else "🔴"
                    direction = "Men olganman (qaytarishim kerak)" if d["direction"] == "took" else "Men berganman (olishim kerak)"
                    msg += (
                        f"{emoji} <b>{d['person_name']}</b> — {format_money(float(d['amount']))}\n"
                        f"   <i>{direction}</i>\n\n"
                    )

            if debts["urgent"]:
                msg += "🟠 <b>Tayyorlaning:</b>\n"
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
                for d in debts["urgent"]:
                    action = "qaytarishingiz" if d["direction"] == "took" else "olishingiz"
                    msg += (
                        f"📅 <b>{d['days_left']} kun</b> qoldi — {d['due_date'].strftime('%d.%m.%Y')}\n"
                        f"👤 {d['person_name']} — {format_money(float(d['amount']))}\n"
                        f"   <i>{action}</i>\n\n"
                    )

            if debts["soon"]:
                msg += "🟡 <b>Yaqin kunlarda:</b>\n"
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
                for d in debts["soon"]:
                    action = "qaytarishingiz" if d["direction"] == "took" else "olishingiz"
                    msg += (
                        f"📅 <b>{d['days_left']} kun</b> qoldi — {d['due_date'].strftime('%d.%m.%Y')}\n"
                        f"👤 {d['person_name']} — {format_money(float(d['amount']))}\n"
                        f"   <i>{action}</i>\n\n"
                    )

            if debts["future"]:
                msg += "🔵 <b>1 oydan keyin:</b>\n"
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
                for d in debts["future"]:
                    action = "qaytarishingiz" if d["direction"] == "took" else "olishingiz"
                    msg += (
                        f"📅 30 kun qoldi — {d['due_date'].strftime('%d.%m.%Y')}\n"
                        f"👤 {d['person_name']} — {format_money(float(d['amount']))}\n"
                        f"   <i>{action} kerak</i>\n\n"
                    )

            msg += "Qarzlarni boshqarish uchun 👇"

            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Qarzlar", callback_data="debts")],
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")],
            ])

            try:
                await bot.send_message(
                    chat_id=uid, text=msg,
                    parse_mode="HTML", reply_markup=markup
                )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"⚠️ Qarz eslatmasi yuborilmadi {uid}: {e}")

            await asyncio.sleep(0.1)

        logger.info(f"✅ Qarz eslatmalari: {sent} yuborildi | ❌ {failed} xato")

    except Exception as e:
        logger.error(f"❌ Qarz eslatmasi funksiyasida xato: {e}")


async def send_daily_reminders(bot):
    """Har kuni 20:00 (Toshkent) da bugun xarajat kiritmaganlarga eslatma yuboradi."""
    try:
        async with db_pool.acquire() as conn:
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

            premium = await is_user_premium(user_id)
            if not premium:
                continue

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

            await asyncio.sleep(0.1)

        logger.info(f"✅ Eslatmalar yuborildi: {sent} ta | ❌ Xato: {failed} ta")

    except Exception as e:
        logger.error(f"❌ Eslatma funksiyasida xato: {e}")

# ===================== MCP SERVER =====================

MCP_MANIFEST = {
    "schema_version": "v1",
    "name": "Oson Byudjet",
    "description": "Personal budget management — transactions, summary, debts",
    "tools": [
        {
            "name": "get_transactions",
            "description": "Get transactions for the current month or a custom date range",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "format": "date", "description": "YYYY-MM-DD"},
                },
            },
        },
        {
            "name": "add_transaction",
            "description": "Add a new income or expense transaction",
            "inputSchema": {
                "type": "object",
                "required": ["type", "amount", "category"],
                "properties": {
                    "type":     {"type": "string", "enum": ["income", "expense"]},
                    "amount":   {"type": "number", "description": "Positive amount in UZS"},
                    "category": {"type": "string", "description": "Category name"},
                    "note":     {"type": "string", "description": "Optional note"},
                },
            },
        },
        {
            "name": "get_summary",
            "description": "Get financial summary for the current month (income, expenses, balance, categories)",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "get_debts",
            "description": "Get list of active (unpaid) debts",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ],
}


async def _mcp_get_transactions(user_id: int, params: dict) -> dict:
    start_raw = params.get("start_date")
    end_raw   = params.get("end_date")
    if start_raw and end_raw:
        try:
            txns = await get_transactions_by_date_range(
                user_id,
                date.fromisoformat(start_raw),
                date.fromisoformat(end_raw),
            )
        except ValueError:
            return {"error": "Invalid date format. Use YYYY-MM-DD"}
    else:
        txns = await get_month_transactions(user_id)

    return {
        "transactions": [
            {
                "id":       t["id"],
                "type":     t["type"],
                "amount":   float(t["amount"]),
                "category": t.get("category", ""),
                "note":     t.get("note", ""),
                "date":     t["date"].isoformat() if hasattr(t["date"], "isoformat") else str(t["date"]),
            }
            for t in txns
        ],
        "count": len(txns),
    }


async def _mcp_add_transaction(user_id: int, params: dict) -> dict:
    txn_type = params.get("type")
    amount   = params.get("amount")
    category = params.get("category", "Boshqa")
    note     = params.get("note", "")

    if txn_type not in ("income", "expense"):
        return {"error": "type must be 'income' or 'expense'"}
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"error": "amount must be a positive number"}
    if amount <= 0:
        return {"error": "amount must be positive"}

    balances   = await get_balances(user_id)
    balance_id = balances[0]["id"] if balances else None

    tx_id = await add_transaction(user_id, txn_type, amount, category, note, balance_id)
    return {"success": True, "transaction_id": tx_id}


async def _mcp_get_summary(user_id: int, params: dict) -> dict:
    txns   = await get_month_transactions(user_id)
    stats  = calc_stats(txns)
    budget = await get_budget(user_id)

    cat_stats: dict = {}
    for t in txns:
        if t["type"] == "expense":
            cat = t.get("category", "Boshqa")
            cat_stats[cat] = cat_stats.get(cat, 0) + float(t["amount"])

    return {
        "month":      datetime.now().strftime("%B %Y"),
        "income":     stats["income"],
        "expenses":   stats["expenses"],
        "balance":    stats["balance"],
        "budget":     budget,
        "categories": [
            {
                "category": cat,
                "amount":   amt,
                "pct":      int(amt / stats["expenses"] * 100) if stats["expenses"] else 0,
            }
            for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1])
        ],
    }


async def _mcp_get_debts(user_id: int, params: dict) -> dict:
    debts = await get_debts(user_id)
    return {
        "debts": [
            {
                "id":          d["id"],
                "person_name": d["person_name"],
                "amount":      float(d["amount"]),
                "direction":   d["direction"],
                "due_date":    d["due_date"].isoformat() if d.get("due_date") else None,
                "note":        d.get("note", ""),
            }
            for d in debts
        ],
        "count": len(debts),
    }


_MCP_TOOLS = {
    "get_transactions": _mcp_get_transactions,
    "add_transaction":  _mcp_add_transaction,
    "get_summary":      _mcp_get_summary,
    "get_debts":        _mcp_get_debts,
}


async def mcp_manifest_handler(request: web.Request) -> web.Response:
    return web.json_response(MCP_MANIFEST)


async def mcp_tool_handler(request: web.Request) -> web.Response:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.json_response({"error": "Unauthorized"}, status=401)

    user_id = await validate_mcp_token(auth[7:])
    if not user_id:
        return web.json_response({"error": "Invalid or expired token"}, status=401)

    tool_name = request.match_info["tool_name"]
    handler   = _MCP_TOOLS.get(tool_name)
    if handler is None:
        return web.json_response(
            {"error": f"Unknown tool: {tool_name}",
             "available": list(_MCP_TOOLS.keys())},
            status=404,
        )

    try:
        params = await request.json()
    except Exception:
        params = {}

    try:
        result = await handler(user_id, params)
    except Exception as e:
        logger.exception("MCP tool error: %s", tool_name)
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response(result)


# ── Proper MCP Streamable HTTP endpoint (JSON-RPC 2.0) ──────────────────────
# Compatible with Claude Desktop 0.7+, Claude Code, and any MCP client.
# Config: { "url": "https://your-app.render.com/mcp",
#           "headers": { "Authorization": "Bearer <token>" } }

_MCP_TOOLS_SCHEMA = [
    {
        "name": "get_transactions",
        "description": "Get transactions for the current month or a custom date range",
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "format": "date", "description": "Start date YYYY-MM-DD (optional)"},
                "end_date":   {"type": "string", "format": "date", "description": "End date YYYY-MM-DD (optional)"},
            },
        },
    },
    {
        "name": "add_transaction",
        "description": "Add a new income or expense transaction",
        "inputSchema": {
            "type": "object",
            "required": ["type", "amount", "category"],
            "properties": {
                "type":     {"type": "string", "enum": ["income", "expense"]},
                "amount":   {"type": "number", "description": "Positive amount in UZS"},
                "category": {"type": "string"},
                "note":     {"type": "string"},
            },
        },
    },
    {
        "name": "get_summary",
        "description": "Get financial summary for the current month (income, expenses, balance, top categories)",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_debts",
        "description": "Get list of active (unpaid) debts",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _jsonrpc_ok(req_id, result):
    return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": result})


def _jsonrpc_err(req_id, code, message):
    return web.json_response(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def mcp_jsonrpc_handler(request: web.Request) -> web.Response:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.Response(
            text='{"error":"Unauthorized"}',
            content_type="application/json",
            status=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = await validate_mcp_token(auth[7:])
    if not user_id:
        return _jsonrpc_err(None, -32000, "Invalid or expired token")

    try:
        body = await request.json()
    except Exception:
        return _jsonrpc_err(None, -32700, "Parse error")

    method  = body.get("method", "")
    req_id  = body.get("id")
    params  = body.get("params") or {}

    # ── initialize ─────────────────────────────────────────────────────────
    if method == "initialize":
        return _jsonrpc_ok(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": "Oson Byudjet", "version": "1.0.0"},
        })

    # ── notifications (fire-and-forget, no response body needed) ───────────
    if method.startswith("notifications/"):
        return web.Response(status=204)

    # ── tools/list ─────────────────────────────────────────────────────────
    if method == "tools/list":
        return _jsonrpc_ok(req_id, {"tools": _MCP_TOOLS_SCHEMA})

    # ── tools/call ─────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        handler   = _MCP_TOOLS.get(tool_name)
        if handler is None:
            return _jsonrpc_err(req_id, -32601, f"Unknown tool: {tool_name}")
        try:
            tool_result = await handler(user_id, arguments)
            return _jsonrpc_ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False, indent=2)}]
            })
        except Exception as e:
            logger.exception("MCP tools/call error: %s", tool_name)
            return _jsonrpc_err(req_id, -32000, str(e))

    return _jsonrpc_err(req_id, -32601, f"Method not found: {method}")


# ===================== WEBHOOK =====================

async def health(request):
    return web.Response(text="✅ Oson Byudjet Bot is alive!", status=200)

async def webhook_handler(request, application):
    data   = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(status=200)

async def main():
    webhook_path = f"/webhook/{BOT_TOKEN}"
    bot_ref = {"app": None}

    async def dynamic_webhook(request):
        if bot_ref["app"] is None:
            return web.Response(status=503, text="Bot initializing, retry shortly")
        return await webhook_handler(request, bot_ref["app"])

    # 1. Portni BIRINCHI ochamiz — Render shu yerda portni aniqlaydi
    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_post(webhook_path, dynamic_webhook)
    web_app.router.add_get("/.well-known/mcp.json", mcp_manifest_handler)
    web_app.router.add_post("/mcp/tools/{tool_name}", mcp_tool_handler)
    web_app.router.add_post("/mcp", mcp_jsonrpc_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"🚀 Port {PORT} ochildi — bot ishga tushmoqda...", flush=True)

    # 2. DB — 45 soniya timeout
    try:
        print("🔄 [1/5] DB ulanmoqda...", flush=True)
        await asyncio.wait_for(init_db(), timeout=45)
        print("✅ [2/5] DB tayyor!", flush=True)
    except asyncio.TimeoutError:
        print("❌ [1/5] DB ulanishi 45 soniyada timeout!", flush=True)
        await asyncio.Event().wait()
        return
    except Exception as e:
        print(f"❌ [1/5] DB xatolik: {e}", flush=True)
        await asyncio.Event().wait()
        return

    # 3. Bot handlers
    print("🔄 [3/5] Telegram bot qurilmoqda...", flush=True)
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("oxirgi", recent_command))
    app.add_handler(CommandHandler("mcp_token", mcp_token_command))
    app.add_handler(CommandHandler("testreminder", admin_test_reminder))
    app.add_handler(CommandHandler("adminstats", admin_stats))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # 4. Telegram ulanishi — 30 soniya timeout
    try:
        print("🔄 [4/5] Telegram ulanmoqda...", flush=True)
        await asyncio.wait_for(app.initialize(), timeout=30)
        await asyncio.wait_for(app.start(), timeout=30)
        print("✅ [4/5] Telegram tayyor!", flush=True)
    except asyncio.TimeoutError:
        print("❌ [4/5] Telegram ulanishi 30 soniyada timeout!", flush=True)
        await asyncio.Event().wait()
        return
    except Exception as e:
        print(f"❌ [4/5] Telegram xatolik: {e}", flush=True)
        await asyncio.Event().wait()
        return

    bot_ref["app"] = app  # endi 200 qaytariladi

    # 5. Webhook va scheduler
    try:
        await app.bot.set_webhook(url=f"{WEBHOOK_URL}{webhook_path}")
        print(f"✅ [5/5] Webhook set: {WEBHOOK_URL}{webhook_path}", flush=True)
    except Exception as e:
        print(f"⚠️ Webhook xatolik (bot ishlaydi): {e}", flush=True)

    scheduler = AsyncIOScheduler(timezone=pytz.timezone("Asia/Tashkent"))
    scheduler.add_job(send_daily_reminders, trigger="cron", hour=20, minute=0,
                      args=[app.bot], id="daily_reminder", replace_existing=True)
    scheduler.add_job(send_debt_reminders, trigger="cron", hour=9, minute=0,
                      args=[app.bot], id="debt_reminder", replace_existing=True)
    scheduler.add_job(send_weekly_reports, trigger="cron", day_of_week="mon",
                      hour=9, minute=1, args=[app.bot], id="weekly_report",
                      replace_existing=True)
    scheduler.add_job(send_monthly_ai_summaries, trigger="cron", day=1, hour=9,
                      minute=2, args=[app.bot], id="monthly_ai_summary",
                      replace_existing=True)
    scheduler.start()
    print("🎉 Bot to'liq ishga tushdi!", flush=True)

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
