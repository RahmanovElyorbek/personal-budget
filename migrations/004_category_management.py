"""
migrations/004_category_management.py — MCP Bosqich 5 uchun DB o'zgarishi.

MUAMMO:
  Bosqich 5 (create_category/update_category/delete_category) uchun ikki
  narsa yetishmayotgan edi:
  1. categories jadvalida `color` ustuni yo'q (spec create_category/
     update_category uchun color parametrini so'raydi).
  2. Tizim kategoriyasini (telegram_id IS NULL, hammaga umumiy) bitta
     foydalanuvchi "o'chirmoqchi" bo'lganda — uni HAQIQATDA o'chirib
     bo'lmaydi (boshqa hamma foydalanuvchiga ham yo'qolib qoladi).
     Spec buni "faqat hide/show toggle" deb belgilagan, lekin buni
     TO'G'RI (faqat SHU foydalanuvchi uchun) qilish uchun alohida
     jadval kerak — categories.is_hidden bitta umumiy ustun bo'lgani
     uchun ishlatib bo'lmaydi (u o'rnatilsa HAMMA uchun berkinadi).

NIMA QO'SHILADI:
  categories jadvaliga:
    - color TEXT DEFAULT NULL
  Yangi jadval:
    - hidden_categories (telegram_id, category_id, hidden_at) — PRIMARY
      KEY (telegram_id, category_id). Bu yerda qator borligi — o'sha
      foydalanuvchi o'sha kategoriyani (odatda tizim kategoriyasini)
      berkitgani degani, boshqa hech kimga ta'sir qilmaydi.

XAVFSIZLIK:
  - Barcha operatsiyalar IDEMPOTENT (IF NOT EXISTS) — bir necha marta
    ishga tushirilsa ham xato bermaydi.
  - Mavjud categories qatorlariga TEGILMAYDI, faqat yangi ustun
    qo'shiladi (mavjud qatorlar color=NULL oladi — bu xavfsiz, "rang
    tanlanmagan" degani).
  - Hech qanday DROP/DELETE avtomatik ishlamaydi — faqat 'down' buyrug'i
    bilan, va u ham ochiq tasdiqlashni talab qiladi.

ISHGA TUSHIRISH (Windows/Linux/Mac — bir xil, faqat Python kerak):
    python migrations/004_category_management.py up
    python migrations/004_category_management.py down     # rollback

DATABASE_URL muhit o'zgaruvchisidan olinadi (bot ishlatadigan bilan bir xil).
PowerShell'da oldindan o'rnatish:
    $env:DATABASE_URL = "postgresql://..."
    python migrations/004_category_management.py up

YANGILANISH: bu o'zgarishlar endi budget_bot_webhook.py ichidagi init_db()
ga HAM qo'shildi (xuddi shu idempotent shaklda) — shuning uchun Render'da
keyingi deploy'da avtomatik ishlaydi, qo'lda ishga tushirish shart emas.
"""

import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

UP_STATEMENTS = [
    (
        "categories.color ustuni",
        "ALTER TABLE categories ADD COLUMN IF NOT EXISTS color TEXT DEFAULT NULL",
    ),
    (
        "hidden_categories jadvali",
        """
        CREATE TABLE IF NOT EXISTS hidden_categories (
            telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
            category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
            hidden_at   TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (telegram_id, category_id)
        )
        """,
    ),
]

DOWN_STATEMENTS = [
    ("hidden_categories jadvalini o'chirish",
     "DROP TABLE IF EXISTS hidden_categories"),
    ("categories.color ustunini o'chirish",
     "ALTER TABLE categories DROP COLUMN IF EXISTS color"),
]


async def upgrade():
    if not DATABASE_URL:
        print("XATO: DATABASE_URL muhit o'zgaruvchisi topilmadi.")
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Migratsiya boshlandi (UP): 004_category_management\n")
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
            "   - hidden_categories jadvali (barcha berkitilgan kategoriya belgilari)\n"
            "   - categories.color ustuni\n\n"
            "Davom etish uchun --yes flagi bilan qayta ishga tushiring:\n"
            "   python migrations/004_category_management.py down --yes"
        )
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Rollback boshlandi (DOWN): 004_category_management\n")
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
