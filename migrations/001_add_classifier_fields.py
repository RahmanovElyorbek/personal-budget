"""
migrations/001_add_classifier_fields.py — VAZIFA 1/2/3 klassifikatori uchun
DB o'zgarishlari.

NIMA QO'SHILADI:
  transactions jadvaliga:
    - currency    TEXT    NOT NULL DEFAULT 'UZS'
    - source      TEXT             DEFAULT 'ai'   (ai | keyword | ai+keyword | ai-retry)
    - is_deleted  BOOLEAN NOT NULL DEFAULT FALSE
  Yangi jadval:
    - category_corrections (id, telegram_id, original_text, ai_category,
      corrected_category, created_at) — foydalanuvchi kategoriyani qo'lda
      tuzatganda shu yerga yoziladi (lug'atni kengaytirish manbai).

XAVFSIZLIK:
  - Barcha operatsiyalar IDEMPOTENT (IF NOT EXISTS / IF EXISTS) — bir necha
    marta ishga tushirilsa ham xato bermaydi.
  - Mavjud transactions/debts qatorlariga TEGILMAYDI, faqat yangi ustun
    qo'shiladi (mavjud qatorlar yangi ustunlar uchun DEFAULT qiymatni oladi).
  - Hech qanday DROP/DELETE avtomatik ishlamaydi — faqat 'down' buyrug'i
    bilan, va u ham ochiq tasdiqlashni talab qiladi.

ISHGA TUSHIRISH (Windows/Linux/Mac — bir xil, faqat Python kerak):
    python migrations/001_add_classifier_fields.py up
    python migrations/001_add_classifier_fields.py down     # rollback

DATABASE_URL muhit o'zgaruvchisidan olinadi (bot ishlatadigan bilan bir xil).
PowerShell'da oldindan o'rnatish:
    $env:DATABASE_URL = "postgresql://..."
    python migrations/001_add_classifier_fields.py up

Bu skript budget_bot_webhook.py ichidagi init_db() ga ULANMAGAN — avtomatik
ishga tushmaydi. Ko'rib chiqib tasdiqlagandan keyin, xohlasangiz men buni
init_db()ga ham (boshqa migratsiyalar kabi, idempotent ALTER TABLE
IF NOT EXISTS shaklida) qo'shib qo'yaman, shunda u Render'da har deployda
avtomatik ishlaydi — yoki har safar shu skriptni qo'lda ishga tushirasiz.
Buni o'zingiz tanlaysiz.
"""

import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

UP_STATEMENTS = [
    (
        "transactions.currency ustuni",
        """
        ALTER TABLE transactions
            ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'UZS'
        """,
    ),
    (
        "transactions.source ustuni",
        """
        ALTER TABLE transactions
            ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'ai'
        """,
    ),
    (
        "transactions.is_deleted ustuni",
        """
        ALTER TABLE transactions
            ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE
        """,
    ),
    (
        "category_corrections jadvali",
        """
        CREATE TABLE IF NOT EXISTS category_corrections (
            id                  SERIAL PRIMARY KEY,
            telegram_id         BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
            original_text       TEXT NOT NULL,
            ai_category         TEXT,
            corrected_category  TEXT NOT NULL,
            created_at          TIMESTAMP DEFAULT NOW()
        )
        """,
    ),
    (
        "category_corrections(telegram_id) indeksi",
        """
        CREATE INDEX IF NOT EXISTS idx_category_corrections_telegram_id
            ON category_corrections(telegram_id)
        """,
    ),
    (
        "transactions(is_deleted) indeksi (o'chirilmagan yozuvlarni tez filtrlash uchun)",
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_is_deleted
            ON transactions(is_deleted) WHERE is_deleted = FALSE
        """,
    ),
]

# Rollback teskari tartibda bajariladi. Ustun/jadvallarni DROP qilish
# ULARDAGI MA'LUMOTNI YO'QOTADI — shuning uchun down() ochiq tasdiqlash
# so'raydi (pastga qarang).
DOWN_STATEMENTS = [
    ("transactions(is_deleted) indeksini o'chirish",
     "DROP INDEX IF EXISTS idx_transactions_is_deleted"),
    ("category_corrections(telegram_id) indeksini o'chirish",
     "DROP INDEX IF EXISTS idx_category_corrections_telegram_id"),
    ("category_corrections jadvalini o'chirish",
     "DROP TABLE IF EXISTS category_corrections"),
    ("transactions.is_deleted ustunini o'chirish",
     "ALTER TABLE transactions DROP COLUMN IF EXISTS is_deleted"),
    ("transactions.source ustunini o'chirish",
     "ALTER TABLE transactions DROP COLUMN IF EXISTS source"),
    ("transactions.currency ustunini o'chirish",
     "ALTER TABLE transactions DROP COLUMN IF EXISTS currency"),
]


async def upgrade():
    if not DATABASE_URL:
        print("XATO: DATABASE_URL muhit o'zgaruvchisi topilmadi.")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Migratsiya boshlandi (UP): 001_add_classifier_fields\n")
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
            "⚠️  DIQQAT: rollback quyidagilarni butunlay O'CHIRADI:\n"
            "   - transactions.currency, source, is_deleted ustunlari\n"
            "   - category_corrections jadvali (undagi barcha yozuvlar bilan)\n\n"
            "Davom etish uchun --yes flagi bilan qayta ishga tushiring:\n"
            "   python migrations/001_add_classifier_fields.py down --yes"
        )
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Rollback boshlandi (DOWN): 001_add_classifier_fields\n")
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
