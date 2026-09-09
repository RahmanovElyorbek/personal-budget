"""
tests/conftest.py — MCP tool testlari uchun umumiy fixture'lar.

Haqiqiy PostgreSQL bazasiga ulanadi (TEST_DATABASE_URL yoki DATABASE_URL
muhit o'zgaruvchisidan) — asyncpg orqali ishlaydigan kodni sqlite yoki
mock bilan ishonchli sinab bo'lmaydi, shuning uchun testlar chinakam
Postgres talab qiladi. Har test ikkita test foydalanuvchisi (USER_A,
USER_B) bilan ishlaydi va har testdan OLDIN ularga oid barcha yozuvlar
tozalanadi (testlar bir-biriga ta'sir qilmasligi uchun) — PRODUCTION
BAZASIGA HECH QACHON ULAMANG, testlar jadvallarni tozalab/yaratib turadi.

O'RNATISH (bir martalik, Windows/Linux/Mac bir xil, faqat Postgres kerak):
  1. Mahalliy yoki bo'sh test PostgreSQL bazasi kerak, masalan:
       createdb mcp_test
  2. Muhit o'zgaruvchisini o'rnating (PowerShell):
       $env:TEST_DATABASE_URL = "postgresql://postgres:parol@localhost:5432/mcp_test"
     (Linux/Mac): export TEST_DATABASE_URL="postgresql://postgres:parol@localhost:5432/mcp_test"
  3. pip install -r requirements.txt pytest pytest-asyncio
  4. python -m pytest tests/ -v

TEST_DATABASE_URL (yoki oddiy DATABASE_URL) berilmagan bo'lsa — barcha DB
testlari avtomatik SKIP qilinadi, xato bermaydi (masalan CI'da Postgres
sozlanmagan bo'lsa ham `pytest` yashil holatda tugaydi, faqat "skipped"
deb ko'rsatadi).
"""

import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("WEBHOOK_URL", "https://test.example.com")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ADMIN_ID", "1")
if TEST_DATABASE_URL:
    os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)

import budget_bot_webhook as bot  # noqa: E402

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL (yoki DATABASE_URL) o'rnatilmagan — DB testi o'tkazib yuborildi.",
)

USER_A = 900000001  # asosiy test foydalanuvchisi
USER_B = 900000002  # IDOR/cross-user testlari uchun ikkinchi foydalanuvchi


@pytest_asyncio.fixture(scope="session")
async def _pool():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL yo'q")
    pool = await bot.asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    bot.db_pool = pool
    await bot.init_db()
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def db(_pool):
    """Har test OLDIDAN USER_A/USER_B'ga oid barcha yozuvlarni tozalaydi
    (ON DELETE CASCADE orqali transactions/balances/debts/shaxsiy
    categories/tokenlar ham o'chadi) va ikkalasini qaytadan premium
    holatda yaratadi. Testlar shu fixture'ni parametr sifatida so'rasin."""
    async with _pool.acquire() as conn:
        async with conn.transaction():
            for uid in (USER_A, USER_B):
                await conn.execute("DELETE FROM users WHERE telegram_id = $1", uid)
                await conn.execute(
                    """
                    INSERT INTO users (telegram_id, name, registered_at, is_premium, premium_until)
                    VALUES ($1, $2, NOW(), TRUE, NOW() + INTERVAL '30 days')
                    """,
                    uid, f"Test User {uid}",
                )
    yield _pool
