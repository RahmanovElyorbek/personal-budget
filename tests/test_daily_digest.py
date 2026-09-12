"""
tests/test_daily_digest.py — "Kunlik yakun" (daily digest) eslatma
funksiyasi uchun testlar (haqiqiy PostgreSQL bazasiga ulanadi —
tests/conftest.py'ga qarang).

Qamrov — vazifa spetsifikatsiyasining 7-bo'limidagi 10 ta qabul qilish
mezoni:
  1. Bugun 3 ta xarajat — kategoriyalar to'g'ri foiz bilan, mediana
     bugunni hisobga olmagan holda.
  2. Bugun 8 xil kategoriya — 3 tasi + "Boshqalar".
  3. Kecha ro'yxatdan o'tgan — taqqoslash qatori YO'Q.
  4. 3 oy oldin ro'yxatdan o'tgan, lekin 2 hafta yozuv yo'q — taqqoslash
     qatori YO'Q (registered_at yolg'iz yetarli emas).
  5. Bazaviy 7 kunda bitta 600 000 lik xarajat — mediana buzilmaydi.
  6. Bugungi sarf ±15% ichida — "odatdagidek sarfladingiz".
  7. Bloklangan foydalanuvchi — blocked=TRUE, funksiya xato bermaydi.
  8. 429 (RetryAfter) — kutib qayta yuboriladi, yakunda muvaffaqiyatli.
  9. MCP orqali yozuv — reminder_miss_streak nolga tushadi.
  10. 23:50 (Toshkent) — o'sha kunning o'ziga kiradi (TZ testi).

Ishga tushirish: python -m pytest tests/test_daily_digest.py -v
"""

from datetime import timedelta

import pytest

from tests.conftest import USER_A, USER_B, requires_db

import budget_bot_webhook as bot


async def _seed_expense_on(user_id: int, day, amount: float, category: str = "🍔 Oziq-ovqat"):
    """Berilgan (o'tmishdagi yoki bugungi) kalendar kunga xarajat qo'shadi
    — mavjud _mcp_add_transaction orqali (alohida INSERT yozilmaydi)."""
    res = await bot._mcp_add_transaction(user_id, {
        "type": "expense", "amount": amount, "category": category,
        "date": day.isoformat(),
    })
    assert res.get("success"), res


async def _set_registered_at(user_id: int, when):
    async with bot.db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET registered_at = $1 WHERE telegram_id = $2", when, user_id)


def _tashkent_today():
    tz = bot.pytz.timezone("Asia/Tashkent")
    return bot.datetime.now(tz).date()


# ===================== 1-2. KATEGORIYALAR =====================

@requires_db
async def test_digest_shows_categories_with_correct_pct(db):
    today = _tashkent_today()
    await _set_registered_at(USER_A, bot.datetime.now() - timedelta(days=30))
    await _seed_expense_on(USER_A, today, 60000, "🍔 Oziq-ovqat")  # 100000'ning 60%
    await _seed_expense_on(USER_A, today, 40000, "🚌 Transport")   # 100000'ning 40%

    text = await bot._build_daily_digest_message(USER_A, "Ali", bot.datetime.now() - timedelta(days=30))

    assert "🍔 Oziq-ovqat" in text and "(60%)" in text
    assert "🚌 Transport" in text and "(40%)" in text


@requires_db
async def test_digest_median_excludes_today(db):
    """Bugungi katta xarajat bazaviy 7 kun medianasiga ta'sir qilmasligi
    kerak (baza faqat today-7..today-1 dan hisoblanadi)."""
    today = _tashkent_today()
    await _set_registered_at(USER_A, bot.datetime.now() - timedelta(days=30))
    for i in range(1, 6):  # today-1 .. today-5, 5 kun x 20000
        await _seed_expense_on(USER_A, today - timedelta(days=i), 20000)
    # Bugun juda katta xarajat — mediana buni HISOBGA OLMASLIGI kerak
    await _seed_expense_on(USER_A, today, 900000)

    daily_sums = await bot._mcp_expense_daily_sums(
        USER_A, today - timedelta(days=7), today - timedelta(days=1))
    assert today not in daily_sums
    assert all(v == 20000.0 for v in daily_sums.values())


@requires_db
async def test_digest_more_than_3_categories_grouped_as_boshqalar(db):
    today = _tashkent_today()
    await _set_registered_at(USER_A, bot.datetime.now() - timedelta(days=30))
    cats = ["🍔 Oziq-ovqat", "🚌 Transport", "🏠 Uy-joy", "💊 Salomatlik",
            "🎮 Ko'ngil ochar", "👗 Kiyim-kechak", "📚 Ta'lim", "💡 Kommunal"]
    for i, cat in enumerate(cats):
        await _seed_expense_on(USER_A, today, 10000 * (8 - i), cat)  # kamayish tartibida

    text = await bot._build_daily_digest_message(USER_A, "Ali", bot.datetime.now() - timedelta(days=30))

    shown_cats = sum(1 for c in cats if c in text)
    assert shown_cats == 3
    assert "📦 Boshqalar" in text


# ===================== 3-4. TARIX YETARLIMI =====================

@requires_db
async def test_digest_no_comparison_when_registered_yesterday(db):
    today = _tashkent_today()
    yesterday_dt = bot.datetime.now() - timedelta(days=1)
    await _seed_expense_on(USER_A, today, 50000)

    text = await bot._build_daily_digest_message(USER_A, "Ali", yesterday_dt)

    assert "Odatiy kunlik sarf" not in text


@requires_db
async def test_digest_no_comparison_when_sparse_recent_history(db):
    """3 oy oldin ro'yxatdan o'tgan, lekin oxirgi 7 kunda umuman yozuv
    yo'q — registered_at yolg'iz yetarli emas, 4/7 kun sharti ham kerak."""
    today = _tashkent_today()
    three_months_ago = bot.datetime.now() - timedelta(days=90)
    await _seed_expense_on(USER_A, today, 50000)
    # Bazaviy 7 kunda (today-7..today-1) hech qanday yozuv qo'shilmadi.

    text = await bot._build_daily_digest_message(USER_A, "Ali", three_months_ago)

    assert "Odatiy kunlik sarf" not in text


# ===================== 5. MEDIANA VS OUTLIER =====================

@requires_db
async def test_median_not_skewed_by_single_outlier(db):
    today = _tashkent_today()
    # 4 oddiy kun (20000, 22000, 18000, 21000) + 1 katta (600000) = 5 kun
    amounts = [20000, 22000, 18000, 21000, 600000]
    for i, amt in enumerate(amounts, start=1):
        await _seed_expense_on(USER_A, today - timedelta(days=i), amt)

    daily_sums = await bot._mcp_expense_daily_sums(
        USER_A, today - timedelta(days=7), today - timedelta(days=1))
    assert len(daily_sums) == 5
    median = bot.statistics.median(daily_sums.values())
    # Median 5 ta qiymatning o'rtadagisi (tartiblangan: 18000,20000,21000,22000,600000 -> 21000)
    assert median == 21000.0
    assert median < 100000  # outlier'dan buzilmaganini tasdiqlash


# ===================== 6. NEYTRAL ZONA (±15%) =====================

@requires_db
async def test_digest_neutral_zone_within_15_percent(db):
    today = _tashkent_today()
    await _set_registered_at(USER_A, bot.datetime.now() - timedelta(days=30))
    for i in range(1, 5):  # 4 kun x 50000 -> mediana 50000
        await _seed_expense_on(USER_A, today - timedelta(days=i), 50000)
    await _seed_expense_on(USER_A, today, 55000)  # 50000dan 10% ko'p -> neytral zona

    text = await bot._build_daily_digest_message(USER_A, "Ali", bot.datetime.now() - timedelta(days=30))

    assert "odatdagidek sarfladingiz" in text
    assert "% ko'p" not in text
    assert "% kam" not in text


@requires_db
async def test_digest_shows_pct_outside_neutral_zone(db):
    today = _tashkent_today()
    await _set_registered_at(USER_A, bot.datetime.now() - timedelta(days=30))
    for i in range(1, 5):
        await _seed_expense_on(USER_A, today - timedelta(days=i), 50000)
    await _seed_expense_on(USER_A, today, 85000)  # 50000dan 70% ko'p

    text = await bot._build_daily_digest_message(USER_A, "Ali", bot.datetime.now() - timedelta(days=30))

    assert "odatdagidan 70% ko'p" in text


# ===================== 7-8. YUBORISH VA XATOLAR =====================

class _FakeBotForbidden:
    async def send_message(self, chat_id, **kwargs):
        raise bot.Forbidden("bloklangan")


class _FakeBotRetryThenOk:
    def __init__(self):
        self.calls = 0

    async def send_message(self, chat_id, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise bot.RetryAfter(0)
        return None


@requires_db
async def test_send_logged_message_marks_blocked_on_forbidden(db):
    fake_bot = _FakeBotForbidden()
    ok = await bot._send_logged_message(fake_bot, USER_A, bot.NOTIF_TYPE_DIGEST, text="salom")
    assert ok is False

    async with bot.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT blocked FROM notification_log WHERE user_id = $1 ORDER BY id DESC LIMIT 1", USER_A)
    assert row["blocked"] is True


@requires_db
async def test_send_logged_message_retries_after_429_and_logs_success(db):
    fake_bot = _FakeBotRetryThenOk()
    ok = await bot._send_logged_message(fake_bot, USER_A, bot.NOTIF_TYPE_DIGEST, text="salom")
    assert ok is True
    assert fake_bot.calls == 2  # bitta 429 + bitta muvaffaqiyatli

    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT blocked FROM notification_log WHERE user_id = $1 ORDER BY id DESC", USER_A)
    # 429 urinish alohida qator sifatida YOZILMAGAN — faqat yakuniy natija
    assert len(rows) == 1
    assert rows[0]["blocked"] is False


# ===================== 9. MCP ORQALI KIRITISH — STREAK =====================

@requires_db
async def test_mcp_add_transaction_resets_reminder_miss_streak(db):
    async with bot.db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET reminder_miss_streak = 3 WHERE telegram_id = $1", USER_A)

    res = await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 15000, "category": "🍔 Oziq-ovqat",
    })
    assert res.get("success")

    async with bot.db_pool.acquire() as conn:
        streak_row = await conn.fetchrow(
            "SELECT reminder_miss_streak FROM users WHERE telegram_id = $1", USER_A)
        closed_row = await conn.fetchrow(
            "SELECT last_closed_date FROM user_streaks WHERE telegram_id = $1", USER_A)

    assert streak_row["reminder_miss_streak"] == 0
    tz = bot.pytz.timezone("Asia/Tashkent")
    assert closed_row["last_closed_date"] == bot.datetime.now(tz).date()


# ===================== 10. VAQT MINTAQASI (23:50 chegarasi) =====================

@requires_db
async def test_transaction_at_23_50_tashkent_counts_for_that_day(db):
    """23:50 Toshkent = 18:50 UTC (bir xil kalendar kuni) — get_today_transactions
    va get_spending_overview shu kunning o'ziga tegishli deb topishi kerak."""
    tz = bot.pytz.timezone("Asia/Tashkent")
    today = _tashkent_today()
    late_local = tz.localize(bot.datetime.combine(today, bot.datetime.min.time()) + timedelta(hours=23, minutes=50))
    late_utc_naive = late_local.astimezone(bot.pytz.utc).replace(tzinfo=None)

    async with bot.db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO transactions (telegram_id, type, amount, category, category_id, date, is_deleted)
            VALUES ($1, 'expense', 45000, '🍔 Oziq-ovqat', 1, $2, FALSE)
            """,
            USER_A, late_utc_naive,
        )

    today_txns = await bot.get_today_transactions(USER_A)
    assert len(today_txns) == 1
    assert float(today_txns[0]["amount"]) == 45000.0

    overview = await bot._mcp_get_spending_overview(USER_A, {
        "from_date": today.isoformat(), "to_date": today.isoformat(), "side": "expense",
    })
    assert overview["total"] == 45000.0
