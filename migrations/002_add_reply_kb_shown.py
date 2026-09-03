"""
migrations/002_add_reply_kb_shown.py — VAZIFA 6 uchun DB o'zgarishi.

MUAMMO (VAZIFA 6):
  "Bosh menyu" (doimiy pastki ReplyKeyboard) foydalanuvchiga ko'rsatilgan-
  ligini bildiruvchi bayroq avval FAQAT context.user_data'da (jarayon
  RAM'ida) saqlanardi. Bot Render'da qayta ishga tushganda (deploy,
  uyquga ketib uyg'onish, xotira yetishmasligi va h.k.) barcha
  context.user_data tozalanadi — bayroq "hali ko'rsatilmagan" holatga
  qaytadi va foydalanuvchi keyingi safar "🏠 Bosh menyu" tugmasini
  bosganda (yoki boshqa gate'lardan o'tganda) menyu blok qayta-qayta
  yuboriladi. Aynan shu sababdan foydalanuvchi "Bosh menyu bir necha
  marta ketma-ket takrorlandi" muammosini ko'rgan.

NIMA QO'SHILADI:
  users jadvaliga:
    - reply_kb_shown  BOOLEAN NOT NULL DEFAULT FALSE

XAVFSIZLIK:
  - Operatsiya IDEMPOTENT (IF NOT EXISTS) — bir necha marta ishga
    tushirilsa ham xato bermaydi.
  - Mavjud users qatorlariga TEGILMAYDI, faqat yangi ustun qo'shiladi
    (mavjud qatorlar DEFAULT FALSE qiymatini oladi — ya'ni mavjud
    foydalanuvchilar birinchi navigatsiyada menyuni yana ko'radi, bu
    xavfsiz va kutilgan holat).
  - Hech qanday DROP/DELETE avtomatik ishlamaydi — faqat 'down' buyrug'i
    bilan, va u ham ochiq tasdiqlashni talab qiladi.

ISHGA TUSHIRISH (Windows/Linux/Mac — bir xil, faqat Python kerak):
    python migrations/002_add_reply_kb_shown.py up
    python migrations/002_add_reply_kb_shown.py down     # rollback

DATABASE_URL muhit o'zgaruvchisidan olinadi (bot ishlatadigan bilan bir xil).
PowerShell'da oldindan o'rnatish:
    $env:DATABASE_URL = "postgresql://..."
    python migrations/002_add_reply_kb_shown.py up

YANGILANISH: bu o'zgarish endi budget_bot_webhook.py ichidagi init_db()
ga HAM qo'shildi (xuddi shu idempotent ALTER TABLE IF NOT EXISTS
shaklida) — shuning uchun Render'da keyingi deploy'da avtomatik
ishlaydi, qo'lda ishga tushirish shart emas.

Bu fayl baribir saqlanadi, chunki:
  - Rollback (`down --yes`) faqat shu yerda bor — init_db() faqat
    qo'shadi, hech qachon o'chirmaydi.
  - Production DB'ga Render deploy'dan OLDIN qo'lda tekshirib/ishga
    tushirmoqchi bo'lsangiz ham ishlatishingiz mumkin — natija bir xil.
"""

import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

UP_STATEMENTS = [
    (
        "users.reply_kb_shown ustuni",
        """
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS reply_kb_shown BOOLEAN NOT NULL DEFAULT FALSE
        """,
    ),
]

# Rollback teskari tartibda bajariladi. Ustunni DROP qilish ULARDAGI
# MA'LUMOTNI YO'QOTADI — shuning uchun down() ochiq tasdiqlash so'raydi
# (pastga qarang).
DOWN_STATEMENTS = [
    ("users.reply_kb_shown ustunini o'chirish",
     "ALTER TABLE users DROP COLUMN IF EXISTS reply_kb_shown"),
]


async def upgrade():
    if not DATABASE_URL:
        print("XATO: DATABASE_URL muhit o'zgaruvchisi topilmadi.")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Migratsiya boshlandi (UP): 002_add_reply_kb_shown\n")
        async with conn.transaction():
            for description, sql in UP_STATEMENTS:
                print(f"  -> {description} ...", end=" ")
                await conn.execute(sql)
                print("OK")
        print("\n✅ Migratsiya muvaffaqiyatli yakunlandi.")
    finally:
        await conn.close()


async def downgrade(confirmed: bool):
    if not DATABASE_URL:
        print("XATO: DATABASE_URL muhit o'zgaruvchisi topilmadi.")
        sys.exit(1)

    if not confirmed:
        print(
            "⚠️  DIQQAT: rollback quyidagini butunlay O'CHIRADI:\n"
            "   - users.reply_kb_shown ustuni\n\n"
            "Davom etish uchun --yes flagi bilan qayta ishga tushiring:\n"
            "   python migrations/002_add_reply_kb_shown.py down --yes"
        )
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Rollback boshlandi (DOWN): 002_add_reply_kb_shown\n")
        async with conn.transaction():
            for description, sql in DOWN_STATEMENTS:
                print(f"  -> {description} ...", end=" ")
                await conn.execute(sql)
                print("OK")
        print("\n✅ Rollback muvaffaqiyatli yakunlandi.")
    finally:
        await conn.close()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    if action == "up":
        asyncio.run(upgrade())
    else:
        confirmed = "--yes" in sys.argv[2:]
        asyncio.run(downgrade(confirmed))


if __name__ == "__main__":
    main()
