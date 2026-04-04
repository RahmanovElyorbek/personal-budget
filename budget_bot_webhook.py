"""
💰 Shaxsiy Oylik Budget Telegram Bot — PREMIUM VERSION
=======================================================
- Supabase PostgreSQL database
- 7 kunlik bepul sinov
- Telegram Stars orqali to'lov
"""

import logging
import os
import asyncio
import asyncpg
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)

# ===================== SOZLAMALAR =====================
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL  = os.environ.get("WEBHOOK_URL", "")
PORT         = int(os.environ.get("PORT", 8080))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Telegram Stars narxlari
PRICE_MONTHLY   = 250   # Stars
PRICE_QUARTERLY = 600   # Stars
PRICE_YEARLY    = 2000  # Stars

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
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, name, registered_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (telegram_id) DO UPDATE SET name = $2
        """, telegram_id, name)

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
            "SELECT registered_at FROM users WHERE telegram_id = $1",
            telegram_id
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
                          amount: float, category: str, note: str):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO transactions (telegram_id, type, amount, category, note)
            VALUES ($1, $2, $3, $4, $5)
        """, telegram_id, txn_type, amount, category, note)

async def get_month_transactions(telegram_id: int) -> list:
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
         InlineKeyboardButton("➖ Xarajat", callback_data="add_expense")],
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

def payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Oylik — 25,000 so'm", callback_data="pay_monthly")],
        [InlineKeyboardButton("📆 3 oylik — 60,000 so'm", callback_data="pay_quarterly")],
        [InlineKeyboardButton("🗓 Yillik — 199,000 so'm", callback_data="pay_yearly")],
    ])

# ===================== TO'LOV EKRANI =====================

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

# ===================== HANDLERLAR =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user.id, user.first_name)

    premium = await is_user_premium(user.id)
    if not premium:
        await show_payment_screen(update, context)
        return

    days_left = await get_trial_days_left(user.id)
    txns   = await get_month_transactions(user.id)
    stats  = calc_stats(txns)
    budget = await get_budget(user.id)

    trial_msg = ""
    if days_left > 0:
        trial_msg = f"🎁 Bepul sinov: <b>{days_left} kun qoldi</b>\n"

    text = (
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        f"💰 <b>Oylik Budget Boshqaruvchi</b>\n"
        f"📅 <b>{datetime.now().strftime('%B %Y')}</b>\n"
        f"{trial_msg}"
        f"\n━━━━━━━━━━━━━━━━━━━━\n"
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
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=main_keyboard())

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

    # To'lov tugmalari — premium tekshiruvsiz
    if data == "pay_monthly":
        await context.bot.send_invoice(
            chat_id=user_id,
            title="💰 Oylik Premium",
            description="Budget Bot — 30 kunlik to'liq kirish",
            payload="premium_monthly",
            currency="XTR",
            prices=[LabeledPrice("Oylik", PRICE_MONTHLY)],
        )
        return

    elif data == "pay_quarterly":
        await context.bot.send_invoice(
            chat_id=user_id,
            title="💰 3 Oylik Premium",
            description="Budget Bot — 90 kunlik to'liq kirish",
            payload="premium_quarterly",
            currency="XTR",
            prices=[LabeledPrice("3 Oylik", PRICE_QUARTERLY)],
        )
        return

    elif data == "pay_yearly":
        await context.bot.send_invoice(
            chat_id=user_id,
            title="💰 Yillik Premium",
            description="Budget Bot — 365 kunlik to'liq kirish",
            payload="premium_yearly",
            currency="XTR",
            prices=[LabeledPrice("Yillik", PRICE_YEARLY)],
        )
        return

    # Boshqa tugmalar uchun premium tekshiruv
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
            "awaiting_amount": True
        })
        emoji = "📥" if txn_type == "income" else "📤"
        await query.edit_message_text(
            f"{emoji} <b>Kategoriya:</b> {category}\n\n"
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

    # Agar biror narsa kutilmayotgan bo'lsa — premium tekshiruv
    if not any([
        context.user_data.get("awaiting_amount"),
        context.user_data.get("awaiting_note"),
        context.user_data.get("awaiting_budget"),
    ]):
        premium = await is_user_premium(user_id)
        if not premium:
            await show_payment_screen(update, context)
            return
        await update.message.reply_text("👇 Boshlash uchun /start yuboring.")
        return

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
            await set_budget(user_id, budget)
            context.user_data.pop("awaiting_budget", None)
            await update.message.reply_text(
                f"✅ <b>Oylik budget belgilandi!</b>\n\n"
                f"🎯 Budget: <b>{format_money(budget)}</b>\n\n/start",
                parse_mode="HTML")
        except ValueError:
            await update.message.reply_text("❌ Faqat musbat raqam kiriting.")

async def _save_transaction(user_id, context, note="",
                            reply_fn=None, via_query=None):
    amount   = context.user_data.get("amount")
    category = context.user_data.get("category", "📦 Boshqa")
    txn_type = context.user_data.get("txn_type", "expense")

    for k in ("amount", "category", "txn_type", "awaiting_amount", "awaiting_note"):
        context.user_data.pop(k, None)

    if not amount:
        return

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

# ===================== TO'LOV HANDLERLARI =====================

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload

    if payload == "premium_monthly":
        days, plan = 30, "Oylik"
    elif payload == "premium_quarterly":
        days, plan = 90, "3 Oylik"
    else:
        days, plan = 365, "Yillik"

    await activate_premium(user_id, days)
    await update.message.reply_text(
        f"🎉 <b>To'lov qabul qilindi!</b>\n\n"
        f"✅ {plan} premium faollashtirildi!\n"
        f"📅 {days} kun davomida to'liq foydalanishingiz mumkin.\n\n"
        f"/start — Bosh menyuga o'tish",
        parse_mode="HTML"
    )

# ===================== WEBHOOK SERVER =====================

async def health(request):
    return web.Response(text="✅ Budget Bot is alive!", status=200)

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
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    await app.initialize()
    await app.start()

    webhook_path = f"/webhook/{BOT_TOKEN}"
    await app.bot.set_webhook(url=f"{WEBHOOK_URL}{webhook_path}")
    logger.info(f"✅ Webhook set: {WEBHOOK_URL}{webhook_path}")

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
