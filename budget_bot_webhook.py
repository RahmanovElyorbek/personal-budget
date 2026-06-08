"""
\ud83d\udcb0 Oson Byudjet Telegram Bot \u2014 v6 (Tranzaksiyani tahrirlash/o'chirish qo'shildi)
=======================================================
- Supabase PostgreSQL database
- 7 kunlik bepul sinov
- To'lov tizimi
- Qarzlar ro'yxati
- Balanslar nazorati (tranzaksiya bilan bog'langan!)
- Ovoz orqali kiritish (OpenAI Whisper) \u2014 BIR NECHTA amaliyot bir ovozda
- Chek rasm tahlili (GPT-4o-mini Vision)
- PDF hisobot
- Tranzaksiyani tahrirlash va o'chirish (balans avtomatik tiklanadi)  \u2190 YANGI
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
    "\ud83c\udf54 Oziq-ovqat", "\ud83d\ude8c Transport", "\ud83c\udfe0 Uy-joy", "\ud83d\udc8a Salomatlik",
    "\ud83c\udfae Ko'ngil ochar", "\ud83d\udc57 Kiyim-kechak", "\ud83d\udcda Ta'lim", "\ud83d\udca1 Kommunal",
    "\ud83d\udcf1 Aloqa", "\ud83c\udf81 Sovg'alar", "\ud83c\udfcb\ufe0f Sport", "\u2708\ufe0f Sayohat", "\ud83d\udce6 Boshqa"
]
INCOME_CATEGORIES = [
    "\ud83d\udcbc Maosh", "\ud83d\udcbb Freelance", "\ud83d\udcc8 Investitsiya", "\ud83c\udf81 Sovg'a",
    "\ud83c\udfe6 Bank foizi", "\ud83d\uded2 Sotish", "\ud83d\udce6 Boshqa daromad"
]

BALANCE_TYPES = {
    "cash":  "\ud83d\udcb5 Naqd pul",
    "card":  "\ud83d\udcb3 Karta",
    "bank":  "\ud83c\udfe6 Bank hisobi",
    "other": "\ud83d\udce6 Boshqa",
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
                logger.info(f"\ud83c\udfa4 whisper-1 (uz): '{text}'")
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
                logger.info(f"\ud83c\udfa4 whisper-1 (tr fallback): '{text}'")
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
        "   - 'uch yuz ming' / '\u00fc\u00e7\u00fcz min' / 'uchz min' = 300000\n"
        "   - 'to'rt yuz ming' / 'd\u00f6r\u00fcz min' / 'd\u00f6rt y\u00fcz min' = 400000\n"
        "   - 'besh yuz ming' / 'be\u015f\u00fcz min' = 500000\n"
        "   - 'olti yuz ming' / 'alt\u0131y\u00fcz min' = 600000\n"
        "   - 'million' / 'milyon' = 1000000\n"
        "   MUHIM: Whisper turkcha-ozarbayjon tilida transkripsiya qilishi mumkin. "
        "   '\u00fc\u00e7\u00fcz min' = uch yuz ming (300000), 'd\u00f6r\u00fcz min' = to'rt yuz ming (400000), "
        "   'be\u015f\u00fcz min' = besh yuz ming (500000). Bu xato emas, transliteratsiya.\n"
        "3. category: faqat expense/income uchun. Quyidagidan ANIQ BIRI:\n"
        f"   Xarajat: {expense_cats}\n"
        f"   Daromad: {income_cats}\n"
        "4. note: qisqa izoh (3-8 so'z)\n"
        "5. person: faqat debt uchun \u2014 kim (masalan 'Sardor'). Aks holda null\n\n"
        "MISOL kirish: 'bozordan 600 ming bozorlik qildim, mashinaga 200 ming "
        "yoqilg'i quydirdim, Sardorga 300 ming qarz berdim'\n"
        "MISOL chiqish:\n"
        '{\"transactions\": ['
        '{\"type\":\"expense\",\"amount\":600000,\"category\":\"\ud83c\udf54 Oziq-ovqat\",\"note\":\"Bozorlik\",\"person\":null},'
        '{\"type\":\"expense\",\"amount\":200000,\"category\":\"\ud83d\ude8c Transport\",\"note\":\"Yoqilg\'i\",\"person\":null},'
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
            logger.info(f"\ud83e\udd16 GPT multi response: {content}")

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
                        category = "\ud83d\udce6 Boshqa"
                elif ttype == "income":
                    if category not in INCOME_CATEGORIES:
                        category = "\ud83d\udce6 Boshqa daromad"
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
        "  \u0425\u043b\u0435\u0431\u2192Non, \u041c\u043e\u043b\u043e\u043a\u043e\u2192Sut, \u042f\u0431\u043b\u043e\u043a\u0438\u2192Olma, \u041c\u044f\u0441\u043e\u2192Go'sht, \u0421\u0430\u0445\u0430\u0440\u2192Shakar\n"
        "- Savdo turi:\n"
        "  \u2022 Supermarket/do'kon/bozor \u2192 '\ud83c\udf54 Oziq-ovqat'\n"
        "  \u2022 Dorixona/klinika \u2192 '\ud83d\udc8a Salomatlik'\n"
        "  \u2022 Yoqilg'i/taksi \u2192 '\ud83d\ude8c Transport'\n"
        "  \u2022 Restoran/kafe \u2192 '\ud83c\udfae Ko'ngil ochar'\n"
        "  \u2022 Kommunal to'lov \u2192 '\ud83d\udca1 Kommunal'\n"
        "  \u2022 Aniqlanmasa \u2192 '\ud83d\udce6 Boshqa'\n\n"
        "Misol javob: {\"amount\":247500,\"category\":\"\ud83c\udf54 Oziq-ovqat\","
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
            logger.info(f"\ud83d\udcf8 GPT receipt response: {content}")

            parsed = json.loads(content)

            if "error" in parsed:
                return {"success": False, "error": parsed["error"]}

            amount = float(parsed.get("amount", 0))
            if amount <= 0:
                return {"success": False, "error": "amount_not_detected"}

            category = parsed.get("category", "\ud83d\udce6 Boshqa")
            if category not in EXPENSE_CATEGORIES:
                logger.warning(f"Invalid category from GPT: {category}")
                category = "\ud83d\udce6 Boshqa"

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
    logger.info("\u2705 Database tayyor!")

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
