import os
import re
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import sheets_manager as sheets

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ConversationHandler states
FIRST_NAME, LAST_NAME, POSITION, PHONE = range(4)
BROADCAST_MSG = 10

PHONE_RE = re.compile(r"^\+998\d{9}$")


# ─── KLAVIATURALAR ────────────────────────────────────────────────────────────

def employee_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["✅ Keldi", "🚪 Ketdi"],
         ["📝 Ro'yxatdan o'tish", "ℹ️ Haqida"]],
        resize_keyboard=True,
    )


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["✅ Keldi", "🚪 Ketdi"],
         ["📢 Broadcast", "👥 Ishchilar"],
         ["📊 Bugungi davomat", "ℹ️ Haqida"]],
        resize_keyboard=True,
    )


def get_menu(user_id: int) -> ReplyKeyboardMarkup:
    return admin_menu() if user_id == ADMIN_ID else employee_menu()


# ─── /start ───────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        name = sheets.get_employee_name(user.id)
    except Exception:
        name = None

    if user.id == ADMIN_ID:
        text = (
            f"👨‍💼 Salom, Admin!\n\n"
            "Sizda quyidagi imkoniyatlar mavjud:\n"
            "• 📢 Broadcast — barcha ishchilarga xabar\n"
            "• 👥 Ishchilar — ro'yxatni ko'rish\n"
            "• 📊 Bugungi davomat — kim keldi/ketdi"
        )
    elif name:
        text = f"Xush kelibsiz, {name}! 👋\n\nDavomat belgilash uchun tugmalardan foydalaning."
    else:
        text = (
            f"Salom, {user.first_name}! 👋\n\n"
            "Bu ishchilar boti.\n"
            "Agar siz yangi ishchi bo'lsangiz, «📝 Ro'yxatdan o'tish» tugmasini bosing."
        )

    await update.message.reply_text(text, reply_markup=get_menu(user.id))


# ─── KELDI / KETDI ────────────────────────────────────────────────────────────

async def handle_keldi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        name = sheets.get_employee_name(user.id)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Sheets bilan bog'lanishda xatolik.")
        return

    if not name:
        await update.message.reply_text(
            "⚠️ Siz ro'yxatdan o'tmagansiz.\n«📝 Ro'yxatdan o'tish» tugmasini bosing."
        )
        return

    if sheets.has_action_today(user.id, "Keldi"):
        await update.message.reply_text(
            f"ℹ️ {name}, siz bugun allaqachon kelganingizni belgilagansiz."
        )
        return

    try:
        sheets.save_attendance(user.id, name, "Keldi")
        await update.message.reply_text(
            f"✅ {name}, ishga kelganingiz qayd etildi!\n"
            f"🕐 Vaqt: {datetime.now().strftime('%H:%M')}"
        )
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")


async def handle_ketdi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        name = sheets.get_employee_name(user.id)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Sheets bilan bog'lanishda xatolik.")
        return

    if not name:
        await update.message.reply_text(
            "⚠️ Siz ro'yxatdan o'tmagansiz.\n«📝 Ro'yxatdan o'tish» tugmasini bosing."
        )
        return

    if not sheets.has_action_today(user.id, "Keldi"):
        await update.message.reply_text(
            f"⚠️ {name}, siz bugun «Keldi» belgilamadingiz."
        )
        return

    if sheets.has_action_today(user.id, "Ketdi"):
        await update.message.reply_text(
            f"ℹ️ {name}, siz bugun allaqachon ketganingizni belgilagansiz."
        )
        return

    try:
        sheets.save_attendance(user.id, name, "Ketdi")
        await update.message.reply_text(
            f"🚪 {name}, ishdan ketganingiz qayd etildi!\n"
            f"🕐 Vaqt: {datetime.now().strftime('%H:%M')}"
        )
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")


# ─── HAQIDA ───────────────────────────────────────────────────────────────────

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Ishchilar boti*\n\n"
        "• ✅ *Keldi* — ishga kelganingizni belgilash\n"
        "• 🚪 *Ketdi* — ishdan ketganingizni belgilash\n"
        "• 📝 *Ro'yxatdan o'tish* — yangi ishchilar uchun\n"
        "• 📢 *Broadcast* — admin e'lonlari\n\n"
        "Barcha ma'lumotlar Google Sheetsga saqlanadi.",
        parse_mode="Markdown",
    )


# ─── RO'YXATDAN O'TISH ────────────────────────────────────────────────────────

async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        existing = sheets.get_employee_name(user.id)
    except Exception:
        existing = None

    if existing:
        await update.message.reply_text(
            f"✅ Siz allaqachon ro'yxatdan o'tgansiz!\n👤 {existing}",
            reply_markup=get_menu(user.id),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📝 *Ro'yxatdan o'tish*\n\n"
        "Bekor qilish: /cancel\n\n"
        "1️⃣ Ismingizni kiriting:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return FIRST_NAME


async def reg_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    if len(val) < 2:
        await update.message.reply_text("⚠️ Ism juda qisqa. Qayta kiriting:")
        return FIRST_NAME
    context.user_data["first_name"] = val
    await update.message.reply_text("2️⃣ Familiyangizni kiriting:")
    return LAST_NAME


async def reg_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    if len(val) < 2:
        await update.message.reply_text("⚠️ Familiya juda qisqa. Qayta kiriting:")
        return LAST_NAME
    context.user_data["last_name"] = val
    await update.message.reply_text("3️⃣ Lavozimingizni kiriting\n(masalan: Dasturchi, Buxgalter, Menejer):")
    return POSITION


async def reg_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    if len(val) < 2:
        await update.message.reply_text("⚠️ Lavozim juda qisqa. Qayta kiriting:")
        return POSITION
    context.user_data["position"] = val
    await update.message.reply_text(
        "4️⃣ Telefon raqamingizni kiriting:\n"
        "Format: <code>+998901234567</code>",
        parse_mode="HTML",
    )
    return PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    if not PHONE_RE.match(val):
        await update.message.reply_text(
            "⚠️ Noto'g'ri format. +998 bilan boshlanib, 12 ta raqam bo'lishi kerak.\n"
            "Misol: <code>+998901234567</code>\n\nQayta kiriting:",
            parse_mode="HTML",
        )
        return PHONE

    user = update.effective_user
    context.user_data["phone"] = val
    data = {
        "telegram_id": user.id,
        "first_name":  context.user_data["first_name"],
        "last_name":   context.user_data["last_name"],
        "position":    context.user_data["position"],
        "phone":       val,
    }

    try:
        saved = sheets.save_employee(data)
        full_name = f"{data['first_name']} {data['last_name']}"

        if saved:
            await update.message.reply_text(
                f"🎉 *Tabriklaymiz, {full_name}!*\n\n"
                f"✅ Ro'yxatdan muvaffaqiyatli o'tdingiz.\n"
                f"💼 Lavozim: {data['position']}\n"
                f"📱 Telefon: {data['phone']}",
                parse_mode="Markdown",
                reply_markup=get_menu(user.id),
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🆕 *Yangi ishchi ro'yxatdan o'tdi!*\n\n"
                        f"👤 {full_name}\n"
                        f"💼 {data['position']}\n"
                        f"📱 {data['phone']}\n"
                        f"🆔 TG ID: `{user.id}`"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        else:
            await update.message.reply_text(
                "⚠️ Siz allaqachon ro'yxatdan o'tgansiz!",
                reply_markup=get_menu(user.id),
            )
    except Exception as e:
        logger.error(e)
        await update.message.reply_text(
            "❌ Saqlashda xatolik yuz berdi. Qayta urinib ko'ring.",
            reply_markup=get_menu(user.id),
        )

    context.user_data.clear()
    return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Ro'yxatdan o'tish bekor qilindi.",
        reply_markup=get_menu(update.effective_user.id),
    )
    return ConversationHandler.END


# ─── BROADCAST ────────────────────────────────────────────────────────────────

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 *Broadcast*\n\n"
        "Barcha ishchilarga yuboriladigan xabarni yozing.\n"
        "Bekor qilish: /cancel",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return BROADCAST_MSG


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    try:
        ids = sheets.get_all_employee_ids()
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Sheets bilan bog'lanishda xatolik.", reply_markup=admin_menu())
        return ConversationHandler.END

    if not ids:
        await update.message.reply_text("⚠️ Ro'yxatda hech qanday ishchi yo'q.", reply_markup=admin_menu())
        return ConversationHandler.END

    sent = failed = 0
    for emp_id in ids:
        try:
            await context.bot.send_message(
                chat_id=emp_id,
                text=f"📢 *E'lon:*\n\n{message_text}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Could not send to {emp_id}: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Xabar yuborildi!\n\n"
        f"📨 Muvaffaqiyatli: {sent} ta\n"
        f"❌ Yuborilmadi: {failed} ta",
        reply_markup=admin_menu(),
    )
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Broadcast bekor qilindi.", reply_markup=admin_menu())
    return ConversationHandler.END


# ─── ADMIN: ISHCHILAR ─────────────────────────────────────────────────────────

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun.")
        return
    try:
        employees = sheets.get_all_employees()
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Sheets bilan bog'lanishda xatolik.")
        return

    if not employees:
        await update.message.reply_text("📋 Hozircha ro'yxatda ishchi yo'q.")
        return

    lines = [f"👥 *Ishchilar ro'yxati — {len(employees)} kishi:*\n"]
    for i, emp in enumerate(employees, 1):
        lines.append(
            f"{i}. {emp.get('Ism', '')} {emp.get('Familiya', '')}\n"
            f"   💼 {emp.get('Lavozim', '—')}  📱 {emp.get('Telefon', '—')}"
        )

    # Telegram message limit: 4096 chars; split if needed
    text = "\n".join(lines)
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000], parse_mode="Markdown")


# ─── ADMIN: BUGUNGI DAVOMAT ───────────────────────────────────────────────────

async def today_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun.")
        return
    try:
        records = sheets.get_today_attendance()
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Sheets bilan bog'lanishda xatolik.")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    if not records:
        await update.message.reply_text(f"📊 {today} — hali hech kim belgilamagan.")
        return

    keldi  = [r for r in records if r.get("Holat") == "Keldi"]
    ketdi  = [r for r in records if r.get("Holat") == "Ketdi"]

    lines = [f"📊 *Bugungi davomat — {today}*\n"]
    lines.append(f"✅ *Keldi ({len(keldi)} kishi):*")
    for r in keldi:
        lines.append(f"  • {r['Ism Familiya']} — {r['Vaqt']}")

    lines.append(f"\n🚪 *Ketdi ({len(ketdi)} kishi):*")
    for r in ketdi:
        lines.append(f"  • {r['Ism Familiya']} — {r['Vaqt']}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN .env faylida ko'rsatilmagan!")
    if ADMIN_ID == 0:
        raise ValueError("ADMIN_ID .env faylida ko'rsatilmagan!")

    app = Application.builder().token(token).build()

    reg_handler = ConversationHandler(
        entry_points=[
            CommandHandler("royxat", register_start),
            MessageHandler(filters.Regex("^📝 Ro'yxatdan o'tish$"), register_start),
        ],
        states={
            FIRST_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_first_name)],
            LAST_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_last_name)],
            POSITION:   [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_position)],
            PHONE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_phone)],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )

    broadcast_handler = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", broadcast_start),
            MessageHandler(filters.Regex("^📢 Broadcast$"), broadcast_start),
        ],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(reg_handler)
    app.add_handler(broadcast_handler)
    app.add_handler(CommandHandler("ishchilar", list_employees))
    app.add_handler(CommandHandler("davomat",   today_attendance))
    app.add_handler(MessageHandler(filters.Regex("^✅ Keldi$"),              handle_keldi))
    app.add_handler(MessageHandler(filters.Regex("^🚪 Ketdi$"),              handle_ketdi))
    app.add_handler(MessageHandler(filters.Regex("^👥 Ishchilar$"),          list_employees))
    app.add_handler(MessageHandler(filters.Regex("^📊 Bugungi davomat$"),    today_attendance))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Haqida$"),             about))
    app.add_handler(CommandHandler("keldi", handle_keldi))
    app.add_handler(CommandHandler("ketdi", handle_ketdi))

    logger.info("Bot ishga tushdi ✅")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
