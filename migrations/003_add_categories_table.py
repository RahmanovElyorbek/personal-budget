"""
migrations/003_add_categories_table.py — MCP Bosqich 1 uchun DB o'zgarishi.

MUAMMO:
  Kategoriyalar hozirgacha DB jadvali emas, budget_bot_webhook.py ichidagi
  hardcoded Python ro'yxati (EXPENSE_CATEGORIES/INCOME_CATEGORIES) edi.
  transactions.category — oddiy TEXT ustun, ID/FK emas. Bu MCP orqali
  "category_id bo'yicha qo'sh/filtrla" kabi ishlarni imkonsiz qilardi.

NIMA QO'SHILADI:
  Yangi jadval:
    - categories (id, telegram_id [NULL=tizim kategoriyasi], name, emoji,
      type ['income'/'expense'], parent_id [subkategoriya uchun, hozircha
      hech kim ishlatmagan], is_hidden, created_at)
  Ma'lumot:
    - Mavjud 20 ta hardcoded kategoriya (EXPENSE_CATEGORIES + INCOME_CATEGORIES)
      tizim kategoriyasi sifatida (telegram_id IS NULL) ko'chiriladi.
  transactions jadvaliga:
    - category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL
  Orqaga to'ldirish (backfill):
    - Mavjud transactions.category (matn) qiymati mos tizim kategoriyasi
      bilan solishtirilib, category_id to'ldiriladi.

XAVFSIZLIK:
  - Barcha operatsiyalar IDEMPOTENT (IF NOT EXISTS / NOT EXISTS tekshiruvi
    / faqat category_id IS NULL qatorlarga tegish) — bir necha marta
    ishga tushirilsa ham xato bermaydi, dublikat yaratmaydi.
  - Mavjud transactions.category (matn) ustuniga TEGILMAYDI — u hali ham
    o'qiladi/yoziladi (BOSQICH 2'da add_transaction v2 to'liq category_id'ga
    o'tgach, 1 oydan keyin olib tashlanadi).
  - Hech qanday DROP/DELETE avtomatik ishlamaydi — faqat 'down' buyrug'i
    bilan, va u ham ochiq tasdiqlashni talab qiladi.

ISHGA TUSHIRISH (Windows/Linux/Mac — bir xil, faqat Python kerak):
    python migrations/003_add_categories_table.py up
    python migrations/003_add_categories_table.py down     # rollback

DATABASE_URL muhit o'zgaruvchisidan olinadi (bot ishlatadigan bilan bir xil).
PowerShell'da oldindan o'rnatish:
    $env:DATABASE_URL = "postgresql://..."
    python migrations/003_add_categories_table.py up

YANGILANISH: bu o'zgarishlar endi budget_bot_webhook.py ichidagi init_db()
ga HAM qo'shildi (xuddi shu idempotent shaklda) — shuning uchun Render'da
keyingi deploy'da avtomatik ishlaydi, qo'lda ishga tushirish shart emas.

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

EXPENSE_CATEGORIES = [
    "🍔 Oziq-ovqat", "🚌 Transport", "🏠 Uy-joy", "💊 Salomatlik",
    "🎮 Ko'ngil ochar", "👗 Kiyim-kechak", "📚 Ta'lim", "💡 Kommunal",
    "📱 Aloqa", "🎁 Sovg'alar", "🏋️ Sport", "✈️ Sayohat", "📦 Boshqa"
]
INCOME_CATEGORIES = [
    "💼 Maosh", "💻 Freelance", "📈 Investitsiya", "🎁 Sovg'a",
    "🏦 Bank foizi", "🛒 Sotish", "📦 Boshqa daromad"
]

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS categories (
        id          SERIAL PRIMARY KEY,
        telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
        name        TEXT NOT NULL,
        emoji       TEXT DEFAULT '',
        type        TEXT NOT NULL,
        parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        is_hidden   BOOLEAN NOT NULL DEFAULT FALSE,
        created_at  TIMESTAMP DEFAULT NOW()
    )
"""
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_categories_telegram_id ON categories(telegram_id)"
SEED_SQL = """
    INSERT INTO categories (telegram_id, name, emoji, type)
    SELECT NULL, $1, $2, $3
    WHERE NOT EXISTS (
        SELECT 1 FROM categories WHERE telegram_id IS NULL AND name = $1 AND type = $3
    )
"""
ADD_COLUMN_SQL = """
    ALTER TABLE transactions ADD COLUMN IF NOT EXISTS
        category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL
"""
BACKFILL_SQL = """
    UPDATE transactions t
    SET category_id = c.id
    FROM categories c
    WHERE t.category_id IS NULL
      AND c.telegram_id IS NULL
      AND t.category = (CASE WHEN c.emoji <> '' THEN c.emoji || ' ' || c.name ELSE c.name END)
"""

DOWN_STATEMENTS = [
    ("transactions.category_id ustunini o'chirish",
     "ALTER TABLE transactions DROP COLUMN IF EXISTS category_id"),
    ("categories(telegram_id) indeksini o'chirish",
     "DROP INDEX IF EXISTS idx_categories_telegram_id"),
    ("categories jadvalini o'chirish",
     "DROP TABLE IF EXISTS categories"),
]


async def upgrade():
    if not DATABASE_URL:
        print("XATO: DATABASE_URL muhit o'zgaruvchisi topilmadi.")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Migratsiya boshlandi (UP): 003_add_categories_table\n")
        async with conn.transaction():
            print("  -> categories jadvali ...", end=" ")
            await conn.execute(CREATE_TABLE_SQL)
            print("OK")

            print("  -> categories(telegram_id) indeksi ...", end=" ")
            await conn.execute(CREATE_INDEX_SQL)
            print("OK")

            print("  -> tizim kategoriyalarini ko'chirish ...", end=" ")
            for cat_type, cat_list in (("expense", EXPENSE_CATEGORIES), ("income", INCOME_CATEGORIES)):
                for full_name in cat_list:
                    emoji, name = full_name.split(" ", 1) if " " in full_name else ("", full_name)
                    await conn.execute(SEED_SQL, name, emoji, cat_type)
            print("OK")

            print("  -> transactions.category_id ustuni ...", end=" ")
            await conn.execute(ADD_COLUMN_SQL)
            print("OK")

            print("  -> mavjud yozuvlarni orqaga to'ldirish (backfill) ...", end=" ")
            result = await conn.execute(BACKFILL_SQL)
            print(f"OK ({result})")
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
            "   - transactions.category_id ustuni (barcha bog'lanishlar bilan)\n"
            "   - categories jadvali (tizim + foydalanuvchi kategoriyalari bilan)\n\n"
            "Davom etish uchun --yes flagi bilan qayta ishga tushiring:\n"
            "   python migrations/003_add_categories_table.py down --yes"
        )
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Rollback boshlandi (DOWN): 003_add_categories_table\n")
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
