"""
migrations/005_backfill_category_id.py — eski category_id IS NULL
yozuvlarni categories jadvaliga bog'lash (backfill).

MUAMMO:
  003_add_categories_table.py migratsiyasidagi BACKFILL_SQL faqat TIZIM
  kategoriyalari (categories.telegram_id IS NULL) bilan solishtirgan edi.
  Agar foydalanuvchining o'z SHAXSIY kategoriyasi bo'lsa-yu, eski
  tranzaksiyada matn sifatida saqlangan bo'lsa (masalan, create_category
  orqali "🐶 Uy hayvonlari" yaratilgan, keyin eski add_transaction bilan
  category="🐶 Uy hayvonlari" matni bilan yozilgan tranzaksiya) — bunday
  yozuvlar HECH QACHON category_id olmagan, chunki shaxsiy kategoriyalar
  tekshirilmagan.

  Natijada bu yozuvlar statistika tool'larida (get_summary,
  get_spending_overview) "❓ Aniqlanmagan" sifatida ko'rinadi va JAMIGA
  kiradi (tushib qolmaydi — bu to'g'ri), lekin KATEGORIYA KESIMIDA
  noto'g'ri guruhlanadi (haqiqiy kategoriyasi bor bo'lsa ham).

NIMA QILADI:
  transactions.category_id IS NULL bo'lgan har bir yozuv uchun,
  t.category matn ustunini quyidagilar bilan solishtiradi:
    1. Foydalanuvchining o'z shaxsiy kategoriyasi (categories.telegram_id
       = t.telegram_id) — USTUVOR.
    2. Tizim kategoriyasi (categories.telegram_id IS NULL).
  Moslik: (emoji <> '' ? emoji || ' ' || name : name) = t.category
  (aynan shu format add_transaction/_mcp_resolve_category'da category
  matnini hosil qilishda ishlatiladi).

  Mos kelmagan yozuvlar (masalan, juda eski erkin matn, yoki kategoriya
  keyinchalik o'chirilgan) category_id IS NULL holida qoladi — bular
  statistika tool'larida "❓ Aniqlanmagan" sifatida ko'rsatiladi, jamidan
  tushib qolmaydi (budget_bot_webhook.py _mcp_category_display).

XAVFSIZLIK:
  - IDEMPOTENT: faqat category_id IS NULL qatorlarga tegadi, qayta ishga
    tushirilsa allaqachon to'ldirilgan qatorlarni o'zgartirmaydi.
  - Hech qanday qator O'CHIRILMAYDI, faqat category_id ustuni to'ldiriladi.
  - transactions.category (matn) ustuniga TEGILMAYDI.

ISHGA TUSHIRISH:
    python migrations/005_backfill_category_id.py up --dry-run   # avval shu bilan tekshiring
    python migrations/005_backfill_category_id.py up             # haqiqiy backfill
    python migrations/005_backfill_category_id.py down --yes     # rollback (category_id'ni qayta NULL qiladi)

DATABASE_URL muhit o'zgaruvchisidan olinadi (bot ishlatadigan bilan bir xil).
"""

import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Har bir NULL category_id yozuv qaysi kategoriyaga mos kelishini topadi —
# foydalanuvchining o'z shaxsiy kategoriyasi tizim kategoriyasidan ustun
# (bir xil nom/emoji bo'lgan holatda ham).
MATCH_SQL = """
    SELECT DISTINCT ON (t.id)
        t.id AS tx_id, c.id AS category_id
    FROM transactions t
    JOIN categories c
      ON (c.telegram_id = t.telegram_id OR c.telegram_id IS NULL)
     AND (CASE WHEN c.emoji <> '' THEN c.emoji || ' ' || c.name ELSE c.name END) = t.category
    WHERE t.category_id IS NULL
      AND t.category IS NOT NULL AND t.category <> ''
    ORDER BY t.id, (c.telegram_id IS NOT NULL) DESC
"""

DRY_RUN_SUMMARY_SQL = """
    SELECT
        COUNT(*) AS total_null,
        COUNT(*) FILTER (WHERE t.category IS NULL OR t.category = '') AS empty_text,
        COUNT(*) FILTER (WHERE t.category IS NOT NULL AND t.category <> '') AS has_text
    FROM transactions t
    WHERE t.category_id IS NULL
"""

DRY_RUN_BREAKDOWN_SQL = """
    SELECT
        t.category AS legacy_text,
        COUNT(*) AS cnt,
        MAX(m.category_id) AS would_match_category_id
    FROM transactions t
    LEFT JOIN LATERAL (
        SELECT c.id AS category_id
        FROM categories c
        WHERE (c.telegram_id = t.telegram_id OR c.telegram_id IS NULL)
          AND (CASE WHEN c.emoji <> '' THEN c.emoji || ' ' || c.name ELSE c.name END) = t.category
        ORDER BY (c.telegram_id IS NOT NULL) DESC
        LIMIT 1
    ) m ON TRUE
    WHERE t.category_id IS NULL
    GROUP BY t.category
    ORDER BY cnt DESC
"""

BACKFILL_SQL = f"""
    UPDATE transactions t
    SET category_id = m.category_id
    FROM ({MATCH_SQL}) m
    WHERE t.id = m.tx_id
"""

# down: faqat shu migratsiya to'ldirgan bo'lishi mumkin bo'lgan qatorlarni
# aniqlab bo'lmaydi (003'dagi backfill bilan bir xil natija chiqishi
# mumkin), shuning uchun down --yes category matni bilan MOS KELADIGAN
# barcha category_id'larni yana NULL qiladi — bu 003 va 005 ikkalasining
# ham natijasini bekor qiladi (xavfsiz: category matn ustuni saqlanib
# qoladi, hech narsa yo'qolmaydi, faqat qayta backfill qilish mumkin).
DOWNGRADE_SQL = """
    UPDATE transactions t
    SET category_id = NULL
    WHERE t.category_id IS NOT NULL
      AND t.category IS NOT NULL AND t.category <> ''
"""


async def dry_run():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        summary = await conn.fetchrow(DRY_RUN_SUMMARY_SQL)
        rows = await conn.fetch(DRY_RUN_BREAKDOWN_SQL)

        # DRY_RUN_BREAKDOWN_SQL category_id IS NULL bo'lgan BARCHA yozuvlarni
        # (matni bo'sh/NULL bo'lganlari ham) t.category bo'yicha guruhlab
        # qaytaradi — shuning uchun matched_rows/unmatched_rows'ning yig'indisi
        # summary['total_null']'ga teng, summary['empty_text']'ni qayta
        # qo'shish KERAK EMAS (aks holda ikki marta hisoblanadi).
        matched_rows = sum(r["cnt"] for r in rows if r["would_match_category_id"] is not None)
        unmatched_rows = sum(r["cnt"] for r in rows if r["would_match_category_id"] is None)

        print("DRY-RUN: 005_backfill_category_id\n")
        print(f"  Jami category_id IS NULL yozuvlar : {summary['total_null']}")
        print(f"    - matn bor (category to'ldirilgan) : {summary['has_text']}")
        print(f"    - matn ham bo'sh/NULL               : {summary['empty_text']}")
        print()
        print(f"  Backfill bilan TOPILADIGAN (category_id o'rnatiladi) : {matched_rows}")
        print(f"  Mos kelmay '❓ Aniqlanmagan' bo'lib QOLADIGAN         : {unmatched_rows}")
        print()
        print("  Matn bo'yicha taqsimot (eng ko'p uchraydigan 20 tasi):")
        print(f"  {'legacy category matni':<30} {'soni':>6}  moslik")
        print(f"  {'-'*30} {'-'*6}  {'-'*20}")
        for r in rows[:20]:
            status = f"-> category_id={r['would_match_category_id']}" if r["would_match_category_id"] else "MOS TOPILMADI"
            text = (r["legacy_text"] or "(bo'sh)")[:30]
            print(f"  {text:<30} {r['cnt']:>6}  {status}")
        if len(rows) > 20:
            print(f"  ... yana {len(rows) - 20} xil matn")
        print("\nHaqiqiy backfill uchun --dry-run'siz qayta ishga tushiring:")
        print("    python migrations/005_backfill_category_id.py up")
    finally:
        await conn.close()


async def upgrade():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Migratsiya boshlandi (UP): 005_backfill_category_id\n")
        async with conn.transaction():
            before = await conn.fetchval(
                "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL")
            print(f"  -> backfill'dan oldin category_id IS NULL: {before}")

            print("  -> backfill bajarilmoqda ...", end=" ")
            result = await conn.execute(BACKFILL_SQL)
            print(f"OK ({result})")

            after = await conn.fetchval(
                "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL")
            print(f"  -> backfill'dan keyin category_id IS NULL: {after}")
            print(f"  -> jami to'ldirilgan: {before - after}")
        print("\n✅ Migratsiya muvaffaqiyatli yakunlandi.")
        print(f"   Qolgan {after} ta yozuv haqiqatan kategoriyasiz — statistikada")
        print("   \"❓ Aniqlanmagan\" sifatida ko'rinadi (jamidan tushib qolmaydi).")
    finally:
        await conn.close()


async def downgrade(confirmed: bool):
    if not confirmed:
        print(
            "⚠️  DIQQAT: rollback matnli category ustuniga mos keladigan BARCHA\n"
            "   category_id qiymatlarini (003 va 005 migratsiyalari to'ldirgan)\n"
            "   qayta NULL qiladi. Hech qanday qator/matn o'chmaydi, faqat\n"
            "   category_id bog'lanishi yo'qoladi (qayta backfill qilish mumkin).\n\n"
            "Davom etish uchun --yes flagi bilan qayta ishga tushiring:\n"
            "   python migrations/005_backfill_category_id.py down --yes"
        )
        sys.exit(1)

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        print("Rollback boshlandi (DOWN): 005_backfill_category_id\n")
        async with conn.transaction():
            result = await conn.execute(DOWNGRADE_SQL)
            print(f"  -> category_id'lar NULL qilindi ... OK ({result})")
        print("\n✅ Rollback muvaffaqiyatli yakunlandi.")
    finally:
        await conn.close()


def main():
    if not DATABASE_URL:
        print("XATO: DATABASE_URL muhit o'zgaruvchisi topilmadi.")
        sys.exit(1)

    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]
    if action == "up":
        if "--dry-run" in sys.argv[2:]:
            asyncio.run(dry_run())
        else:
            asyncio.run(upgrade())
    else:
        confirmed = "--yes" in sys.argv[2:]
        asyncio.run(downgrade(confirmed))


if __name__ == "__main__":
    main()
