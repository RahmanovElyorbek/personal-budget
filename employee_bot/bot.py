import os
import logging
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


# ─── YORDAMCHI ────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def main_menu():
    keyboard = [
        ["✅ Keldi", "🚪 Ketdi"],
        ["📝 Ro'yxatdan o'tish", "ℹ️ Haqida"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


# ─── ASOSIY HANDLERLAR ────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = sheets.get_employee_name(user.id)
    if name:
        greeting = f"Xush kelibsiz, {name}! 👋"
    else:
        greeting = (
            f"Salom, {user.first_name}! 👋\n\n"
            "Agar yangi ishchi bo'lsangiz, «📝 Ro'yxatdan o'tish» tugmasini bosing."
        )
    await update.message.reply_text(greeting, reply_markup=main_menu())


async def handle_keldi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = sheets.get_employee_name(user.id)
    if not name:
        await update.message.reply_text(
            "⚠️ Siz ro'yxatdan o'tmagansiz. Avval «📝 Ro'yxatdan o'tish» tugmasini bosing."
        )
        return

    try:
        sheets.save_attendance(user.id, name, "Keldi")
        await update.message.reply_text(
            f"✅ {name}, ishga kelganingiz qayd etildi!\n🕐 Vaqt: {__import__('datetime').datetime.now().strftime('%H:%M')}"
        )
    except Exception as e:
        logger.error(f"Attendance error: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")


async def handle_ketdi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = sheets.get_employee_name(user.id)
    if not name:
        await update.message.reply_text(
            "⚠️ Siz ro'yxatdan o'tmagansiz. Avval «📝 Ro'yxatdan o'tish» tugmasini bosing."
        )
        return

    try:
        sheets.save_attendance(user.id, name, "Ketdi")
        await update.message.reply_text(
            f"🚪 {name}, ishdan ketganingiz qayd etildi!\n🕐 Vaqt: {__import__('datetime').datetime.now().strftime('%H:%M')}"
        )
    except Exception as e:
        logger.error(f"Attendance error: {e}")
        await update.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Ishchilar boti*\n\n"
        "Bu bot quyidagi imkoniyatlarni beradi:\n"
        "• ✅ Ishga kelganingizni belgilash\n"
        "• 🚪 Ishdan ketganingizni belgilash\n"
        "• 📝 Ro'yxatdan o'tish\n"
        "• 📢 Yangiliklar va e'lonlarni olish",
        parse_mode="Markdown",
    )


# ─── RO'YXATDAN O'TISH (ConversationHandler) ──────────────────────────────────

async def register_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = sheets.get_employee_name(user.id)
    if existing:
        await update.message.reply_text(
            f"✅ Siz allaqachon ro'yxatdan o'tgansiz: {existing}",
            reply_markup=main_menu(),
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📝 Ro'yxatdan o'tish boshlandi.\n\n"
        "1️⃣ Ismingizni kiriting:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return FIRST_NAME


async def reg_first_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["first_name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ Familiyangizni kiriting:")
    return LAST_NAME


async def reg_last_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["last_name"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ Lavozimingizni kiriting (masalan: Dasturchi, Buxgalter):")
    return POSITION


async def reg_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["position"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ Telefon raqamingizni kiriting (+998XXXXXXXXX):")
    return PHONE


async def reg_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    context.user_data["phone"] = update.message.text.strip()

    data = {
        "telegram_id": user.id,
        "first_name": context.user_data["first_name"],
        "last_name": context.user_data["last_name"],
        "position": context.user_data["position"],
        "phone": context.user_data["phone"],
    }

    try:
        saved = sheets.save_employee(data)
        if saved:
            full_name = f"{data['first_name']} {data['last_name']}"
            await update.message.reply_text(
                f"🎉 Tabriklaymiz, {full_name}!\n\n"
                f"✅ Ro'yxatdan muvaffaqiyatli o'tdingiz.\n"
                f"💼 Lavozim: {data['position']}\n"
                f"📱 Telefon: {data['phone']}",
                reply_markup=main_menu(),
            )
            # Admin ga xabar berish
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🆕 Yangi ishchi ro'yxatdan o'tdi!\n\n"
                         f"👤 {full_name}\n"
                         f"💼 {data['position']}\n"
                         f"📱 {data['phone']}\n"
                         f"🆔 TG ID: {user.id}",
                )
            except Exception:
                pass
        else:
            await update.message.reply_text(
                "⚠️ Siz allaqachon ro'yxatdan o'tgansiz!",
                reply_markup=main_menu(),
            )
    except Exception as e:
        logger.error(f"Registration error: {e}")
        await update.message.reply_text(
            "❌ Saqlashda xatolik. Qayta urinib ko'ring.",
            reply_markup=main_menu(),
        )

    context.user_data.clear()
    return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Ro'yxatdan o'tish bekor qilindi.",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


# ─── BROADCAST (Admin) ────────────────────────────────────────────────────────

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 Barcha ishchilarga yuboriladigan xabarni yozing:\n\n"
        "Bekor qilish uchun /cancel",
        reply_markup=ReplyKeyboardRemove(),
    )
    return BROADCAST_MSG


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    employee_ids = sheets.get_all_employee_ids()

    if not employee_ids:
        await update.message.reply_text("⚠️ Ro'yxatda hech qanday ishchi yo'q.", reply_markup=main_menu())
        return ConversationHandler.END

    sent = 0
    failed = 0
    for emp_id in employee_ids:
        try:
            await context.bot.send_message(
                chat_id=emp_id,
                text=f"📢 *Admin xabari:*\n\n{message_text}",
                parse_mode="Markdown",
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Could not send to {emp_id}: {e}")
            failed += 1

    await update.message.reply_text(
        f"✅ Xabar yuborildi!\n\n"
        f"📨 Muvaffaqiyatli: {sent}\n"
        f"❌ Yuborilmadi: {failed}",
        reply_markup=main_menu(),
    )
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Broadcast bekor qilindi.", reply_markup=main_menu())
    return ConversationHandler.END


# ─── ADMIN: ISHCHILAR RO'YXATI ────────────────────────────────────────────────

async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bu buyruq faqat admin uchun.")
        return

    try:
        employees = sheets.get_all_employees()
        if not employees:
            await update.message.reply_text("📋 Hozircha ro'yxatda ishchi yo'q.")
            return

        text = f"👥 *Ishchilar ro'yxati ({len(employees)} kishi):*\n\n"
        for i, emp in enumerate(employees, 1):
            text += (
                f"{i}. {emp.get('Ism', '')} {emp.get('Familiya', '')}\n"
                f"   💼 {emp.get('Lavozim', '-')}\n"
                f"   📱 {emp.get('Telefon', '-')}\n\n"
            )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"List employees error: {e}")
        await update.message.reply_text("❌ Ma'lumotlarni olishda xatolik.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN .env faylida ko'rsatilmagan!")

    app = Application.builder().token(token).build()

    # Ro'yxatdan o'tish
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

    # Broadcast
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ishchilar", list_employees))
    app.add_handler(reg_handler)
    app.add_handler(broadcast_handler)
    app.add_handler(MessageHandler(filters.Regex("^✅ Keldi$"), handle_keldi))
    app.add_handler(MessageHandler(filters.Regex("^🚪 Ketdi$"), handle_ketdi))
    app.add_handler(MessageHandler(filters.Regex("^ℹ️ Haqida$"), about))
    app.add_handler(CommandHandler("keldi", handle_keldi))
    app.add_handler(CommandHandler("ketdi", handle_ketdi))

    logger.info("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
