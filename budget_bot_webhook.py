"""
💰 Shaxsiy Oylik Budget Telegram Bot — SUPABASE VERSION
=======================================================
JSON fayl o'rniga PostgreSQL (Supabase) ishlatadi.
Har bir foydalanuvchi ma'lumoti alohida saqlanadi.

Render environment variables:
  - BOT_TOKEN
  - WEBHOOK_URL
  - DATABASE_URL  ← Supabase dan olinadi (yangi!)
"""

import logging
import os
import asyncio
import asyncpg
from datetime import datetime
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

# ===================== SOZLAMALAR =====================
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "")
PORT         = int(os.environ.get("PORT", 8080))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

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

# ===================== DATABASE =====================

# Global connection pool — bir marta ochiladi, qayta-qayta ishlatiladi
db_pool = None

async def init_db():
    """Supabase ga ulanish va jadvallarni yaratish."""
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)

    async with db_pool.acquire() as conn:
        # Users jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                name        TEXT DEFAULT '',
                budget      NUMERIC DEFAULT 0,
                created_at  TIMESTAMP DEFAULT NOW()
            )
        """)

        # Transactions jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id          SERIAL PRIMARY KEY,
                telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
                type        TEXT NOT NULL,
                amount      NUMERIC NOT NULL,
                category    TEXT DEFAULT 'Boshqa',
                note        TEXT DEFAULT '',
                date        TIMESTAMP DEFAULT NOW()
            )
        """)

    logger.info("✅ Database tayyor!")

async def ensure_user(telegram_id: int, name: str = ""):
    """Foydalanuvchi yo'q bo'lsa — yaratadi, bor bo'lsa — nomini yangilaydi."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, name)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET name = $2
        """, telegram_id, name)

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
                          amount: float, category: str, note: str):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transactions (telegram_id, type, amount, category, note)
            VALUES ($1, $2, $3, $4, $5)
        """, telegram_id, txn_type, amount, category, note)

async def get_month_transactions(telegram_id: int) -> list:
    """Joriy oyning tranzaksiyalari."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT type, amount, category, note, date
            FROM transactions
            WHERE telegram_id = $1
              AND DATE_TRUNC('month', date) = DATE_TRUNC('month', NOW())
            ORDER BY date DESC
        """, telegram_id)
        return [dict(r) for r in rows]

async def clear_month_transactions(telegram_id: int):
    """Joriy oyning tranzaksiyalarini o'chirish."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            DELETE FROM transactions
            WHERE telegram_id = $1
              AND DATE_TRUNC('month', date) = DATE_TRUNC('month', NOW())
        """, telegram_id)

# ===================== YORDAMCHI FUNKSIYALAR =====================

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

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Daromad", callback_data="add_income"),
         InlineKeyboardButton("➖ Xarajat",  callback_data="add_expense")],
        [InlineKeyboardButton("📊 Statistika", callback_data="stats"),
         InlineKeyboardButton("💰 Budget belgilash", callback_data="set_budget")],
        [InlineKeyboardButton("📋 Tarix", callback_data="history"),
         InlineKeyboardButton("🗑️ Tozalash", callback_data="clear_month")],
    ])

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

# ===================== HANDLERLAR =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.first_name)

    txns   = await get_month_transactions(user.id)
    stats  = calc_stats(txns)
    budget = await get_budget(user.id)

    text = (
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        f"💰 <b>Oylik Budget Boshqaruvchi</b>\n"
        f"📅 <b>{datetime.now().strftime('%B %Y')}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 Daromad : <b>{format_money(stats['income'])}</b>\n"
        f"📤 Xarajat : <b>{format_money(stats['expenses'])}</b>\n"
        f"💵 Balans  : <b>{format_money(stats['balance'])}</b>\n"
    )
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
    text += "\n👇 Quyidagi tugmalardan birini tanlang:"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Yordam — Budget Bot</b>\n\n"
        "/start — Bosh menyu\n/help — Yordam\n\n"
        "➕ Daromad/Xarajat kiritish\n"
        "📁 Kategoriyalar bo'yicha tasniflash\n"
        "🎯 Oylik budget belgilash\n"
        "📊 Statistika va tahlil\n"
        "⚠️ Budget oshsa ogohlantirish",
        parse_mode="HTML"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user_id = query.from_user.id

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
            "awaiting_amount": True
        })
        emoji = "📥" if txn_type == "income" else "📤"
        await query.edit_message_text(
            f"{emoji} <b>Kategoriya:</b> {category}\n\n"
            f"💬 Miqdorni kiriting (faqat raqam):\n<i>Masalan: 50000</i>",
            parse_mode="HTML")

    elif data == "stats":
        await ensure_user(user_id)
        txns   = await get_month_transactions(user_id)
        stats  = calc_stats(txns)
        budget = await get_budget(user_id)

        cat_stats = {}
        for t in txns:
            if t["type"] == "expense":
                cat = t.get("category", "Boshqa")
                cat_stats[cat] = cat_stats.get(cat, 0) + float(t["amount"])

        msg = (
            f"📊 <b>Statistika — {datetime.now().strftime('%B %Y')}</b>\n\n"
            f"📥 Jami daromad : <b>{format_money(stats['income'])}</b>\n"
            f"📤 Jami xarajat : <b>{format_money(stats['expenses'])}</b>\n"
            f"💵 Sof balans   : <b>{format_money(stats['balance'])}</b>\n"
        )
        if budget > 0:
            used = int(stats['expenses'] / budget * 100) if budget else 0
            rem  = budget - stats['expenses']
            msg += (
                f"\n🎯 <b>Budget holati:</b>\n"
                f"  Belgilangan : {format_money(budget)}\n"
                f"  Sarflangan  : {format_money(stats['expenses'])} ({used}%)\n"
                f"  Qolgan      : {format_money(max(rem, 0))}\n"
            )
            if rem < 0:
                msg += f"  ⚠️ Budget {format_money(abs(rem))} oshib ketdi!\n"
        if cat_stats:
            msg += "\n🏆 <b>Top xarajatlar:</b>\n"
            for cat, amt in sorted(cat_stats.items(), key=lambda x: -x[1])[:5]:
                pct = int(amt / stats['expenses'] * 100) if stats['expenses'] else 0
                msg += f"  {cat}: {format_money(amt)} ({pct}%)\n"

        await query.edit_message_text(msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")]]))

    elif data == "history":
        await ensure_user(user_id)
        txns   = await get_month_transactions(user_id)
        recent = txns[:10]

        if not recent:
            msg = "📋 <b>Bu oyda tranzaksiyalar yo'q.</b>"
        else:
            msg = f"📋 <b>Oxirgi {len(recent)} ta tranzaksiya:</b>\n\n"
            for t in recent:
                emoji = "📥" if t["type"] == "income" else "📤"
                date  = t["date"].strftime("%d.%m") if hasattr(t["date"], "strftime") else str(t["date"])[:10]
                note  = f" — {t['note']}" if t.get("note") else ""
                msg  += f"{emoji} <b>{format_money(float(t['amount']))}</b>\n  📁 {t.get('category','Boshqa')} | 📅 {date}{note}\n\n"

        await query.edit_message_text(msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Bosh menyu", callback_data="back_main")]]))

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

    elif data == "back_main":
        await ensure_user(user_id)
        txns  = await get_month_transactions(user_id)
        stats = calc_stats(txns)
        await query.edit_message_text(
            f"🏠 <b>Bosh menyu</b>\n\n"
            f"📅 {datetime.now().strftime('%B %Y')}\n"
            f"📥 {format_money(stats['income'])}\n"
            f"📤 {format_money(stats['expenses'])}\n"
            f"💵 {format_money(stats['balance'])}",
            parse_mode="HTML", reply_markup=main_keyboard())

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()

    if context.user_data.get("awaiting_amount"):
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
            await ensure_user(user_id)
            await set_budget(user_id, budget)
            context.user_data.pop("awaiting_budget", None)
            await update.message.reply_text(
                f"✅ <b>Oylik budget belgilandi!</b>\n\n"
                f"🎯 Budget: <b>{format_money(budget)}</b>\n\n/start",
                parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")
    else:
        await update.message.reply_text("👇 Boshlash uchun /start yuboring.")

async def _save_transaction(user_id, context, note="",
                            reply_fn=None, via_query=None):
    amount   = context.user_data.get("amount")
    category = context.user_data.get("category", "📦 Boshqa")
    txn_type = context.user_data.get("txn_type", "expense")

    for k in ("amount", "category", "txn_type", "awaiting_amount", "awaiting_note"):
        context.user_data.pop(k, None)

    if not amount:
        return

    await ensure_user(user_id)
    await add_transaction(user_id, txn_type, amount, category, note)

    txns   = await get_month_transactions(user_id)
    stats  = calc_stats(txns)
    budget = await get_budget(user_id)

    emoji  = "📥" if txn_type == "income" else "📤"
    note_t = f"\n📝 Izoh: {note}" if note else ""

    msg = (
        f"✅ <b>{'Daromad' if txn_type=='income' else 'Xarajat'} saqlandi!</b>\n\n"
        f"{emoji} Miqdor    : <b>{format_money(amount)}</b>\n"
        f"📁 Kategoriya: {category}{note_t}\n\n"
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

# ===================== WEBHOOK SERVER =====================

async def health(request):
    return web.Response(text="✅ Budget Bot is alive!", status=200)

async def webhook_handler(request, application):
    data   = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return web.Response(status=200)

async def main():
    # Database ulanish
    await init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    await app.initialize()
    await app.start()

    webhook_path = f"/webhook/{BOT_TOKEN}"
    await app.bot.set_webhook(url=f"{WEBHOOK_URL}{webhook_path}")
    logger.info(f"✅ Webhook set: {WEBHOOK_URL}{webhook_path}")

    web_app = web.Application()
    web_app.router.add_get("/",            health)
    web_app.router.add_post(webhook_path,  lambda r: webhook_handler(r, app))

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🚀 Server started on port {PORT}")

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
