"""
tests/test_mcp_tools.py — MCP tool'lari uchun integratsion testlar
(haqiqiy PostgreSQL bazasiga ulanadi — tests/conftest.py'ga qarang).

Har muhim tool uchun kamida: muvaffaqiyatli holat, not_found, validatsiya
xatosi va boshqa foydalanuvchi ID'si bilan urinish (IDOR himoyasi)
tekshiriladi. MCP tool'lari HTTP 403 emas — {"error": "not_found", ...}
JSON qaytaradi (JSON-RPC/REST natija har doim 200 bilan qaytadi, xato
tool natijasi ichida bo'ladi) — bu ataylab shunday: AI xatoni javob
tanasidan o'qib, foydalanuvchiga tushuntirishi kerak.

Ishga tushirish: tests/conftest.py docstring'iga qarang (TEST_DATABASE_URL
kerak). DB yo'q bo'lsa bu fayldagi testlar SKIP qilinadi.

    python -m pytest tests/test_mcp_tools.py -v
"""

import hashlib
import hmac

import pytest

from tests.conftest import USER_A, USER_B, requires_db

import budget_bot_webhook as bot


# ===================== AUTH — pure logic (DB shart emas) =====================

def test_pkce_challenge_matches_rfc7636_vector():
    # RFC 7636 §A.1 rasmiy test vektori
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    expected = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert bot._pkce_challenge_from_verifier(verifier) == expected


def test_rate_limiter_allows_up_to_max_then_blocks():
    bot._mcp_rate_limit_state.clear()
    token = "test-rate-token"
    results = [bot._mcp_rate_limited(token) for _ in range(bot._MCP_RATE_LIMIT_MAX + 5)]
    assert not any(results[: bot._MCP_RATE_LIMIT_MAX])
    assert all(results[bot._MCP_RATE_LIMIT_MAX :])


def test_pct_change_helper():
    assert bot._mcp_pct_change(150, 100) == 50.0
    assert bot._mcp_pct_change(50, 100) == -50.0
    assert bot._mcp_pct_change(0, 0) is None
    assert bot._mcp_pct_change(100, 0) == 100.0


# ===================== AUTH — DB bilan =====================

@requires_db
async def test_create_and_resolve_mcp_api_token(db):
    token = await bot.create_mcp_api_token(USER_A, "Asosiy akkaunt")
    resolved = await bot.resolve_mcp_auth(token)
    assert resolved is not None
    assert resolved["user_id"] == USER_A
    assert resolved["auth_method"] == "api_token"


@requires_db
async def test_resolve_mcp_auth_rejects_unknown_token(db):
    resolved = await bot.resolve_mcp_auth("this-token-does-not-exist")
    assert resolved is None


@requires_db
async def test_revoked_token_no_longer_resolves(db):
    token = await bot.create_mcp_api_token(USER_A, "Vaqtinchalik")
    ok = await bot.revoke_mcp_api_token(USER_A, "Vaqtinchalik")
    assert ok is True
    assert await bot.resolve_mcp_auth(token) is None


@requires_db
async def test_revoke_wrong_label_returns_false(db):
    await bot.create_mcp_api_token(USER_A, "Asosiy")
    ok = await bot.revoke_mcp_api_token(USER_A, "Mavjud bo'lmagan nom")
    assert ok is False


# ===================== KATEGORIYALAR (Bosqich 1 + 5) =====================

@requires_db
async def test_list_categories_has_20_system_categories(db):
    res = await bot._mcp_list_categories(USER_A, {"limit": 200})
    system_items = [c for c in res["items"]]
    assert res["summaries"]["count"] >= 20
    assert any(c["name"] == "Oziq-ovqat" for c in system_items)


@requires_db
async def test_create_category_requires_type_without_parent(db):
    res = await bot._mcp_create_category(USER_A, {"name": "Mening kategoriyam"})
    assert res.get("error") == "validation_error"


@requires_db
async def test_create_category_inherits_type_from_parent(db):
    parent = await bot._mcp_list_categories(USER_A, {"search": "Transport"})
    parent_id = parent["items"][0]["id"]
    res = await bot._mcp_create_category(USER_A, {"name": "Taksi", "parent_id": parent_id})
    assert res["success"] is True
    assert res["type"] == "expense"
    assert res["parent_id"] == parent_id


@requires_db
async def test_update_category_own_success(db):
    created = await bot._mcp_create_category(USER_A, {"name": "Eski nom", "type": "expense"})
    res = await bot._mcp_update_category(USER_A, {"id": created["category_id"], "name": "Yangi nom"})
    assert res["success"] is True
    assert res["name"] == "Yangi nom"


@requires_db
async def test_update_category_system_category_rejected(db):
    sys_cat = await bot._mcp_list_categories(USER_A, {"search": "Oziq-ovqat"})
    cat_id = sys_cat["items"][0]["id"]
    res = await bot._mcp_update_category(USER_A, {"id": cat_id, "name": "Boshqa nom"})
    assert res.get("error") == "validation_error"


@requires_db
async def test_update_category_other_users_category_not_found(db):
    """IDOR himoyasi: USER_B USER_A'ning shaxsiy kategoriyasini tahrirlay olmaydi."""
    created = await bot._mcp_create_category(USER_A, {"name": "Faqat A uchun", "type": "expense"})
    res = await bot._mcp_update_category(USER_B, {"id": created["category_id"], "name": "Bosqinchi"})
    assert res.get("error") == "not_found"


@requires_db
async def test_delete_category_own_deletes_and_moves_transactions(db):
    cat1 = await bot._mcp_create_category(USER_A, {"name": "O'chiriladigan", "type": "expense"})
    cat2 = await bot._mcp_create_category(USER_A, {"name": "Yangi manzil", "type": "expense"})
    tx = await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 1000, "category_id": cat1["category_id"],
    })
    res = await bot._mcp_delete_category(USER_A, {
        "id": cat1["category_id"], "move_to_category_id": cat2["category_id"],
    })
    assert res["action"] == "deleted"
    moved_tx = await bot._mcp_get_transaction(USER_A, {"id": tx["transaction_id"]})
    assert moved_tx["category_id"] == cat2["category_id"]


@requires_db
async def test_delete_category_system_hides_instead_of_deleting(db):
    sys_cat = await bot._mcp_list_categories(USER_A, {"search": "Sport"})
    cat_id = sys_cat["items"][0]["id"]
    res = await bot._mcp_delete_category(USER_A, {"id": cat_id})
    assert res["action"] == "hidden"
    # Faqat USER_A uchun berkitilgan bo'lishi kerak
    listing_a = await bot._mcp_list_categories(USER_A, {"limit": 200})
    listing_b = await bot._mcp_list_categories(USER_B, {"limit": 200})
    assert not any(c["id"] == cat_id for c in listing_a["items"])
    assert any(c["id"] == cat_id for c in listing_b["items"])


# ===================== TRANZAKSIYALAR (Bosqich 2) =====================

@requires_db
async def test_add_transaction_with_category_id(db):
    cat = await bot._mcp_list_categories(USER_A, {"search": "Oziq-ovqat"})
    cat_id = cat["items"][0]["id"]
    res = await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 50000, "category_id": cat_id, "comment": "Bozor",
    })
    assert res["success"] is True
    assert res["category_id"] == cat_id


@requires_db
async def test_add_transaction_legacy_category_text_backward_compat(db):
    res = await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 15000, "category": "🍔 Oziq-ovqat",
    })
    assert res["success"] is True
    assert res["category_id"] is None  # matn orqali — category_id resolve qilinmagan


@requires_db
async def test_add_transaction_validation_error_bad_type(db):
    res = await bot._mcp_add_transaction(USER_A, {"type": "invalid", "amount": 100})
    assert res.get("error") == "validation_error"


@requires_db
async def test_add_transaction_validation_error_negative_amount(db):
    res = await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": -500})
    assert res.get("error") == "validation_error"


@requires_db
async def test_add_transaction_syncs_balance(db):
    async with bot.db_pool.acquire() as conn:
        bal_id = await conn.fetchval(
            "INSERT INTO balances (telegram_id, name, type, amount) VALUES ($1,'Naqd','cash',100000) RETURNING id",
            USER_A)
    await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 30000, "category": "Boshqa", "balance_id": bal_id,
    })
    async with bot.db_pool.acquire() as conn:
        amount = await conn.fetchval("SELECT amount FROM balances WHERE id = $1", bal_id)
    assert float(amount) == 70000.0


@requires_db
async def test_get_transaction_not_found(db):
    res = await bot._mcp_get_transaction(USER_A, {"id": 99999999})
    assert res.get("error") == "not_found"


@requires_db
async def test_get_transaction_other_users_transaction_not_found(db):
    """IDOR himoyasi: USER_B USER_A'ning tranzaksiyasini o'qiy olmaydi."""
    tx = await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa"})
    res = await bot._mcp_get_transaction(USER_B, {"id": tx["transaction_id"]})
    assert res.get("error") == "not_found"


@requires_db
async def test_update_transaction_partial_field_only_changes_given(db):
    tx = await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa", "comment": "eski"})
    res = await bot._mcp_update_transaction(USER_A, {"id": tx["transaction_id"], "amount": 2000})
    assert res["amount"] == 2000.0
    assert res["note"] == "eski"  # o'zgartirilmagan


@requires_db
async def test_delete_transaction_soft_deletes_and_hides_from_list(db):
    tx = await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 5000, "category": "Boshqa"})
    res = await bot._mcp_delete_transaction(USER_A, {"id": tx["transaction_id"]})
    assert res["success"] is True
    async with bot.db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_deleted FROM transactions WHERE id = $1", tx["transaction_id"])
    assert row["is_deleted"] is True
    listing = await bot._mcp_list_transactions(USER_A, {})
    all_ids = [t["id"] for day in listing["items"] for t in day["transactions"]]
    assert tx["transaction_id"] not in all_ids


@requires_db
async def test_delete_transaction_reverses_balance_effect(db):
    async with bot.db_pool.acquire() as conn:
        bal_id = await conn.fetchval(
            "INSERT INTO balances (telegram_id, name, type, amount) VALUES ($1,'Naqd','cash',100000) RETURNING id",
            USER_A)
    tx = await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 20000, "category": "Boshqa", "balance_id": bal_id,
    })
    await bot._mcp_delete_transaction(USER_A, {"id": tx["transaction_id"]})
    async with bot.db_pool.acquire() as conn:
        amount = await conn.fetchval("SELECT amount FROM balances WHERE id = $1", bal_id)
    assert float(amount) == 100000.0


@requires_db
async def test_delete_transaction_not_found_for_other_user(db):
    tx = await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa"})
    res = await bot._mcp_delete_transaction(USER_B, {"id": tx["transaction_id"]})
    assert res.get("error") == "not_found"


@requires_db
async def test_replace_transaction_atomic_swap(db):
    tx = await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa"})
    res = await bot._mcp_replace_transaction(USER_A, {
        "id": tx["transaction_id"], "type": "income", "amount": 99999, "category": "Boshqa daromad",
    })
    assert res["type"] == "income"
    assert res["amount"] == 99999.0
    old = await bot._mcp_get_transaction(USER_A, {"id": tx["transaction_id"]})
    assert old.get("error") == "not_found"  # eskisi soft-delete bo'ldi


@requires_db
async def test_list_transactions_groups_by_day(db):
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa"})
    await bot._mcp_add_transaction(USER_A, {"type": "income", "amount": 2000, "category": "Boshqa daromad"})
    res = await bot._mcp_list_transactions(USER_A, {"limit": 10})
    assert len(res["items"]) >= 1
    assert res["summaries"]["count"] == 2


@requires_db
async def test_get_reports_filters_by_type(db):
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa"})
    await bot._mcp_add_transaction(USER_A, {"type": "income", "amount": 2000, "category": "Boshqa daromad"})
    res = await bot._mcp_get_reports(USER_A, {"type": "income"})
    assert res["summaries"]["count"] == 1
    assert res["items"][0]["type"] == "income"


@requires_db
async def test_get_reports_rejects_debt_type(db):
    res = await bot._mcp_get_reports(USER_A, {"type": "debt"})
    assert res.get("error") == "validation_error"


# ===================== QARZLAR (Bosqich 4) =====================

@requires_db
async def test_add_debt_gave_direction(db):
    res = await bot._mcp_add_debt(USER_A, {
        "amount": 500000, "person_name": "Ali", "direction": "gave",
    })
    assert res["success"] is True


@requires_db
async def test_get_debts_summary_has_no_per_person_breakdown(db):
    await bot._mcp_add_debt(USER_A, {"amount": 500000, "person_name": "Ali", "direction": "gave"})
    await bot._mcp_add_debt(USER_A, {"amount": 200000, "person_name": "Vali", "direction": "took"})
    res = await bot._mcp_get_debts_summary(USER_A, {})
    assert res["gave"]["total"] == 500000.0
    assert res["gave"]["people_count"] == 1
    assert res["took"]["total"] == 200000.0
    assert "person_name" not in str(res)  # summary hech qanday ism qaytarmasligi kerak


@requires_db
async def test_get_debts_detail_grouped_by_person(db):
    await bot._mcp_add_debt(USER_A, {"amount": 100000, "person_name": "Ali", "direction": "gave"})
    await bot._mcp_add_debt(USER_A, {"amount": 50000, "person_name": "Ali", "direction": "gave"})
    res = await bot._mcp_get_debts_detail(USER_A, {})
    ali = next(p for p in res["items"] if p["person_name"] == "Ali")
    assert ali["gave_total"] == 150000.0
    assert len(ali["debts"]) == 2


@requires_db
async def test_return_debt_full_closes_it(db):
    added = await bot._mcp_add_debt(USER_A, {"amount": 300000, "person_name": "Sardor", "direction": "gave"})
    detail = await bot._mcp_get_debts_detail(USER_A, {})
    debt_id = detail["items"][0]["debts"][0]["id"]
    res = await bot._mcp_return_debt(USER_A, {"id": debt_id})
    assert res["success"] is True
    detail_after = await bot._mcp_get_debts_detail(USER_A, {})
    assert detail_after["summaries"]["count"] == 0


@requires_db
async def test_return_debt_not_found_for_other_user(db):
    await bot._mcp_add_debt(USER_A, {"amount": 100000, "person_name": "Ali", "direction": "gave"})
    detail = await bot._mcp_get_debts_detail(USER_A, {})
    debt_id = detail["items"][0]["debts"][0]["id"]
    res = await bot._mcp_return_debt(USER_B, {"id": debt_id})
    assert res.get("error") == "not_found"


@requires_db
async def test_partial_return_debt_reduces_remaining(db):
    await bot._mcp_add_debt(USER_A, {"amount": 500000, "person_name": "Ali", "direction": "gave"})
    detail = await bot._mcp_get_debts_detail(USER_A, {})
    debt_id = detail["items"][0]["debts"][0]["id"]
    res = await bot._mcp_partial_return_debt(USER_A, {"id": debt_id, "amount": 200000})
    assert res["applied"] == 200000.0
    assert res["remaining"] == 300000.0
    assert res["fully_paid"] is False


@requires_db
async def test_partial_return_debt_overpay_closes_fully(db):
    await bot._mcp_add_debt(USER_A, {"amount": 100000, "person_name": "Ali", "direction": "gave"})
    detail = await bot._mcp_get_debts_detail(USER_A, {})
    debt_id = detail["items"][0]["debts"][0]["id"]
    res = await bot._mcp_partial_return_debt(USER_A, {"id": debt_id, "amount": 999999})
    assert res["applied"] == 100000.0
    assert res["fully_paid"] is True


@requires_db
async def test_list_closed_debts_shows_only_paid(db):
    await bot._mcp_add_debt(USER_A, {"amount": 100000, "person_name": "Ali", "direction": "gave"})
    detail = await bot._mcp_get_debts_detail(USER_A, {})
    debt_id = detail["items"][0]["debts"][0]["id"]
    await bot._mcp_return_debt(USER_A, {"id": debt_id})
    closed = await bot._mcp_list_closed_debts(USER_A, {})
    assert closed["summaries"]["count"] == 1
    assert closed["items"][0]["person_name"] == "Ali"


# ===================== STATISTIKA (Bosqich 3) =====================

@requires_db
async def test_get_summary_basic_math(db):
    await bot._mcp_add_transaction(USER_A, {"type": "income", "amount": 1000000, "category": "Maosh"})
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 300000, "category": "Boshqa"})
    res = await bot._mcp_get_summary(USER_A, {})
    assert res["income"] == 1000000.0
    assert res["expenses"] == 300000.0
    assert res["balance"] == 700000.0


@requires_db
async def test_get_spending_overview_percentages_sum_to_100(db):
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 300000, "category": "🍔 Oziq-ovqat"})
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 700000, "category": "🚌 Transport"})
    today = bot.datetime.now(bot.pytz.timezone("Asia/Tashkent")).date()
    res = await bot._mcp_get_spending_overview(USER_A, {
        "from_date": today.replace(day=1).isoformat(), "to_date": today.isoformat(), "side": "expense",
    })
    total_pct = sum(c["pct"] for c in res["categories"])
    assert 99.0 <= total_pct <= 100.1  # yaxlitlashdan kelib chiqadigan minimal farq


# ===================== SOZLAMALAR (Bosqich 6) =====================

@requires_db
async def test_set_budget(db):
    res = await bot._mcp_set_budget(USER_A, {"amount": 2000000})
    assert res["success"] is True
    profile = await bot._mcp_get_profile(USER_A, {})
    assert profile["monthly_budget"] == 2000000.0


@requires_db
async def test_set_budget_rejects_category_id(db):
    res = await bot._mcp_set_budget(USER_A, {"amount": 100000, "category_id": 1})
    assert res.get("error") == "validation_error"


@requires_db
async def test_notification_settings_roundtrip(db):
    res = await bot._mcp_update_notification_settings(USER_A, {"hour": 22})
    assert res["enabled"] is True
    assert res["hour"] == 22
    got = await bot._mcp_get_notification_settings(USER_A, {})
    assert got == {"enabled": True, "hour": 22}


@requires_db
async def test_notification_settings_invalid_hour_rejected(db):
    res = await bot._mcp_update_notification_settings(USER_A, {"hour": 15})
    assert res.get("error") == "validation_error"


@requires_db
async def test_notification_settings_disable(db):
    await bot._mcp_update_notification_settings(USER_A, {"hour": 20})
    res = await bot._mcp_update_notification_settings(USER_A, {"enabled": False})
    assert res["enabled"] is False
    assert res["hour"] is None


# ===================== PDF HISOBOT (Bosqich 6) =====================

@requires_db
async def test_generate_pdf_report_returns_valid_pdf(db):
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 50000, "category": "🍔 Oziq-ovqat"})
    today = bot.datetime.now(bot.pytz.timezone("Asia/Tashkent")).date()
    res = await bot._mcp_generate_pdf_report(USER_A, {
        "from_date": today.replace(day=1).isoformat(), "to_date": today.isoformat(),
    })
    assert res["success"] is True
    assert "download_url" in res
    token = res["download_url"].rsplit("/", 1)[-1].removesuffix(".pdf")
    pdf_bytes, _expires = bot._mcp_pdf_reports[token]
    assert pdf_bytes[:4] == b"%PDF"  # haqiqiy PDF magic bytes


@requires_db
async def test_generate_pdf_report_validation_error(db):
    res = await bot._mcp_generate_pdf_report(USER_A, {"from_date": "2026-01-31", "to_date": "2026-01-01"})
    assert res.get("error") == "validation_error"


# ===================== QOLGAN TOOL'LAR — to'liq qamrov uchun =====================

@requires_db
async def test_whoami_returns_premium_true_for_test_user(db):
    res = await bot._mcp_whoami(USER_A, {})
    assert res["user_id"] == USER_A
    assert res["is_premium"] is True
    assert "scopes" in res


@requires_db
async def test_get_transactions_legacy_v1_tool(db):
    """Eski (v1) get_transactions — hozir ham _MCP_TOOLS ro'yxatida,
    orqaga moslik uchun. Avvalgi PR'da t['id'] KeyError bug'i tuzatilgan
    edi — shu yerda shu holat qayta buzilmasligini tekshiramiz."""
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category": "Boshqa"})
    res = await bot._mcp_get_transactions(USER_A, {})
    assert res["count"] == 1
    assert "id" in res["transactions"][0]


@requires_db
async def test_get_used_categories_orders_by_usage(db):
    cat = await bot._mcp_list_categories(USER_A, {"search": "Transport"})
    cat_id = cat["items"][0]["id"]
    for _ in range(3):
        await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 1000, "category_id": cat_id})
    res = await bot._mcp_get_used_categories(USER_A, {"limit": 5})
    assert res["items"][0]["id"] == cat_id
    assert res["items"][0]["usage_count"] == 3


@requires_db
async def test_list_subcategories_empty_for_leaf_category(db):
    cat = await bot._mcp_list_categories(USER_A, {"search": "Transport"})
    cat_id = cat["items"][0]["id"]
    res = await bot._mcp_list_subcategories(USER_A, {"category_id": cat_id})
    assert res["items"] == []  # hech kim hali subkategoriya yaratmagan — bu XATO EMAS


@requires_db
async def test_list_subcategories_returns_children(db):
    parent = await bot._mcp_list_categories(USER_A, {"search": "Transport"})
    parent_id = parent["items"][0]["id"]
    child = await bot._mcp_create_category(USER_A, {"name": "Taksi", "parent_id": parent_id})
    res = await bot._mcp_list_subcategories(USER_A, {"category_id": parent_id})
    assert len(res["items"]) == 1
    assert res["items"][0]["id"] == child["category_id"]


@requires_db
async def test_get_category_stats_builds_tree(db):
    parent = await bot._mcp_list_categories(USER_A, {"search": "Transport"})
    parent_id = parent["items"][0]["id"]
    child = await bot._mcp_create_category(USER_A, {"name": "Taksi", "parent_id": parent_id})
    await bot._mcp_add_transaction(USER_A, {"type": "expense", "amount": 40000, "category_id": child["category_id"]})
    today = bot.datetime.now(bot.pytz.timezone("Asia/Tashkent")).date()
    res = await bot._mcp_get_category_stats(USER_A, {
        "from_date": today.replace(day=1).isoformat(), "to_date": today.isoformat(), "side": "expense",
    })
    transport_node = next(c for c in res["categories"] if c["category_id"] == parent_id)
    assert transport_node["amount"] == 40000.0
    assert len(transport_node["children"]) == 1
    assert transport_node["children"][0]["category_id"] == child["category_id"]


@requires_db
async def test_get_balance_timeseries_matches_current_balance(db):
    async with bot.db_pool.acquire() as conn:
        bal_id = await conn.fetchval(
            "INSERT INTO balances (telegram_id, name, type, amount) VALUES ($1,'Naqd','cash',500000) RETURNING id",
            USER_A)
    await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 100000, "category": "Boshqa", "balance_id": bal_id,
    })
    today = bot.datetime.now(bot.pytz.timezone("Asia/Tashkent")).date()
    res = await bot._mcp_get_balance_timeseries(USER_A, {
        "from_date": today.isoformat(), "to_date": today.isoformat(),
    })
    assert res["current_total_balance"] == 400000.0
    # oxirgi nuqta balansi hozirgi umumiy balansga teng bo'lishi kerak
    # (bugundan keyin boshqa tranzaksiya yo'q)
    assert res["points"][-1]["balance"] == 400000.0


@requires_db
async def test_compare_periods_shows_change_pct(db):
    tz = bot.pytz.timezone("Asia/Tashkent")
    today = bot.datetime.now(tz).date()
    yesterday = today - bot.timedelta(days=1)
    await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 100000, "category": "Boshqa", "date": today.isoformat(),
    })
    await bot._mcp_add_transaction(USER_A, {
        "type": "expense", "amount": 50000, "category": "Boshqa", "date": yesterday.isoformat(),
    })
    res = await bot._mcp_compare_periods(USER_A, {
        "period_a_from": yesterday.isoformat(), "period_a_to": yesterday.isoformat(),
        "period_b_from": today.isoformat(), "period_b_to": today.isoformat(),
        "side": "expense",
    })
    assert res["period_a"]["total"] == 50000.0
    assert res["period_b"]["total"] == 100000.0
    assert res["total_change_pct"] == 100.0
