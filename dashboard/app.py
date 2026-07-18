"""
Oson Budget — Web Kabinet
Streamlit dashboard: premium foydalanuvchilar + admin panel
"""
import os
from datetime import datetime, date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import streamlit as st
from psycopg2.extras import RealDictCursor

# ─────────────────────────── Config ───────────────────────────────────
st.set_page_config(
    page_title="💰 Oson Budget",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="auto",
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "0"))

MONTHS = {
    1: "Yanvar",  2: "Fevral",  3: "Mart",    4: "Aprel",
    5: "May",     6: "Iyun",    7: "Iyul",    8: "Avgust",
    9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr",
}

BALANCE_TYPES = {
    "cash":  ("💵", "#F39C12"),
    "card":  ("💳", "#3498DB"),
    "bank":  ("🏦", "#9B59B6"),
    "other": ("📦", "#95A5A6"),
}

PALETTE = ["#6C63FF", "#2ECC71", "#E74C3C", "#F39C12",
           "#3498DB", "#9B59B6", "#1ABC9C", "#E67E22"]

# ─────────────────────────── DB ───────────────────────────────────────
def _new_conn():
    return psycopg2.connect(DATABASE_URL)

def _conn():
    c = st.session_state.get("_db")
    if c is None or c.closed:
        c = _new_conn()
        st.session_state["_db"] = c
        return c
    try:
        with c.cursor() as cur:
            cur.execute("SELECT 1")
        return c
    except Exception:
        c = _new_conn()
        st.session_state["_db"] = c
        return c

def q(sql: str, params=()):
    conn = _conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

def q1(sql: str, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None

def run(sql: str, params=()):
    conn = _conn()
    with conn.cursor() as cur:
        cur.execute(sql, params)
    conn.commit()

# ─────────────────────────── Utils ───────────────────────────────────
def fmt(n) -> str:
    if n is None:
        return "0 so'm"
    return f"{float(n):,.0f}".replace(",", " ") + " so'm"

def is_premium(u: dict) -> bool:
    if not u:
        return False
    if u.get("is_premium") and u.get("premium_until"):
        deadline = u["premium_until"]
        if not deadline.tzinfo:
            deadline = deadline.replace(tzinfo=None)
        if deadline > datetime.now():
            return True
    reg = u.get("registered_at", datetime.min)
    return datetime.now() < reg + timedelta(days=7)

def chart_layout(fig, height=320):
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=4, r=4, t=8, b=4),
        height=height,
        font=dict(color="#C9D1D9"),
        legend=dict(font=dict(size=11)),
    )
    return fig

# ─────────────────────────── CSS ─────────────────────────────────────
CSS = """
<style>
/* Sidebar */
[data-testid="stSidebar"] {
    background: #0D1117 !important;
    border-right: 1px solid #21262D;
}
/* KPI card */
.kpi-card {
    background: #161B22;
    border-radius: 12px;
    padding: 16px 20px;
    border-left: 4px solid #6C63FF;
    margin-bottom: 6px;
}
.kpi-label { color:#8B949E; font-size:0.78rem; margin:0; letter-spacing:.5px; }
.kpi-value { color:#E6EDF3; font-size:1.5rem; font-weight:700; margin:4px 0 0; line-height:1.2; }
.kpi-delta { font-size:0.76rem; margin:3px 0 0; }
/* Section title */
.sec-title {
    color:#C9D1D9; font-size:1rem; font-weight:600;
    border-bottom:1px solid #21262D;
    padding-bottom:5px; margin:20px 0 12px;
}
/* Login card */
.login-wrap { max-width:420px; margin:60px auto 0; }
/* Debt tag */
.tag-red  { background:#3D1A1A; color:#F85149; border-radius:6px;
            padding:2px 8px; font-size:.75rem; }
.tag-green{ background:#1A3D1A; color:#3FB950; border-radius:6px;
            padding:2px 8px; font-size:.75rem; }
/* Mobile */
@media (max-width: 640px) {
    .kpi-value { font-size:1.1rem; }
    [data-testid="stSidebar"] { display:none; }
}
</style>
"""

def kpi(label: str, value: str, delta=None, color="#6C63FF"):
    d_html = ""
    if delta is not None:
        clr = "#3FB950" if delta <= 0 else "#F85149"
        arr = "▼" if delta <= 0 else "▲"
        d_html = f'<p class="kpi-delta" style="color:{clr}">{arr} {fmt(abs(delta))}</p>'
    st.markdown(
        f'<div class="kpi-card" style="border-left-color:{color}">'
        f'<p class="kpi-label">{label}</p>'
        f'<p class="kpi-value">{value}</p>{d_html}</div>',
        unsafe_allow_html=True,
    )

def sec(title: str):
    st.markdown(f'<p class="sec-title">{title}</p>', unsafe_allow_html=True)

# ─────────────────────────── Auth ─────────────────────────────────────
def try_login(uid: int, code: str) -> str:
    """
    Returns: "ok" | "invalid_code" | "no_user" | "not_premium"
    """
    code_row = q1(
        "SELECT id FROM login_codes "
        "WHERE user_id=%s AND code=%s AND expires_at>NOW() AND used=FALSE",
        (uid, code),
    )
    if not code_row:
        return "invalid_code"

    # Bir marta ishlatiladi
    run("UPDATE login_codes SET used=TRUE WHERE id=%s", (code_row["id"],))

    user = q1(
        "SELECT telegram_id, name, registered_at, premium_until, is_premium, budget "
        "FROM users WHERE telegram_id=%s",
        (uid,),
    )
    if not user:
        return "no_user"

    admin = uid == ADMIN_ID
    if not admin and not is_premium(user):
        return "not_premium"

    st.session_state["user_id"]   = uid
    st.session_state["user_name"] = user.get("name") or "Foydalanuvchi"
    st.session_state["is_admin"]  = admin
    st.session_state["budget"]    = float(user.get("budget") or 0)
    return "ok"

# ─────────────────────────── Login page ──────────────────────────────
def page_login():
    st.markdown(CSS, unsafe_allow_html=True)
    _, col, _ = st.columns([1, 1.5, 1])
    with col:
        st.markdown("## 💰 Oson Budget")
        st.markdown("### Web Kabinet")
        st.markdown("---")

        with st.form("lf"):
            tg_id = st.text_input("🆔 Telegram ID", placeholder="1234567890")
            code  = st.text_input("🔑 Kirish kodi (6 raqam)", placeholder="123456", max_chars=6)
            sub   = st.form_submit_button("Kirish →", use_container_width=True, type="primary")

        if sub:
            tid = tg_id.strip()
            c   = code.strip()
            if not tid.isdigit():
                st.error("❌ Telegram ID faqat raqamlardan iborat")
            elif len(c) != 6 or not c.isdigit():
                st.error("❌ Kod 6 ta raqamdan iborat bo'lishi kerak")
            else:
                res = try_login(int(tid), c)
                if res == "ok":
                    st.rerun()
                elif res == "invalid_code":
                    st.error("❌ Kod noto'g'ri, muddati o'tgan yoki allaqachon ishlatilgan")
                elif res == "not_premium":
                    st.warning(
                        "⚠️ Web kabinet faqat **premium** foydalanuvchilar uchun.\n\n"
                        "Botdan premium obuna rasmiylashtiring."
                    )
                else:
                    st.error("❌ Foydalanuvchi topilmadi")

        st.markdown("---")
        st.info("Botdan **🌐 Web-kabinet** tugmasini bosib, ID va kodni oling.", icon="ℹ️")

# ─────────────────────────── Sidebar ─────────────────────────────────
def render_sidebar() -> str:
    with st.sidebar:
        st.markdown("## 💰 Oson Budget")
        name = st.session_state.get("user_name", "")
        uid  = st.session_state.get("user_id", "")
        st.markdown(f"👤 **{name}**")
        st.caption(f"ID: `{uid}`")
        st.markdown("---")

        pages = ["📊 Umumiy", "📋 Tranzaksiyalar", "💸 Qarzlar", "💳 Balanslar"]
        if st.session_state.get("is_admin"):
            pages.append("👑 Admin Panel")

        page = st.radio("", pages, label_visibility="collapsed")
        st.markdown("---")

        if st.button("🚪 Chiqish", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    return page

# ─────────────────────────── Overview ────────────────────────────────
def page_overview():
    uid = st.session_state["user_id"]
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("## 📊 Umumiy ko'rinish")

    months_rows = q(
        "SELECT DISTINCT EXTRACT(YEAR FROM date)::int AS y, "
        "EXTRACT(MONTH FROM date)::int AS m "
        "FROM transactions WHERE telegram_id=%s "
        "ORDER BY y DESC, m DESC LIMIT 12",
        (uid,),
    )
    now = datetime.now()
    opts = [(r["y"], r["m"]) for r in months_rows]
    if (now.year, now.month) not in opts:
        opts.insert(0, (now.year, now.month))

    labels = [f"{MONTHS[m]} {y}" for y, m in opts]
    sel_i  = st.selectbox("📅 Oy:", range(len(labels)), format_func=lambda i: labels[i])
    sy, sm = opts[sel_i]

    rows = q(
        "SELECT type, amount, category, date "
        "FROM transactions "
        "WHERE telegram_id=%s AND EXTRACT(YEAR FROM date)=%s AND EXTRACT(MONTH FROM date)=%s "
        "ORDER BY date",
        (uid, sy, sm),
    )

    if not rows:
        st.info(f"📭 {labels[sel_i]} davri uchun tranzaksiyalar yo'q")
        return

    df = pd.DataFrame(rows)
    df["amount"] = df["amount"].astype(float)
    income   = df[df["type"] == "income"]["amount"].sum()
    expenses = df[df["type"] == "expense"]["amount"].sum()
    balance  = income - expenses

    # Previous month for delta
    pm = sm - 1 if sm > 1 else 12
    py = sy if sm > 1 else sy - 1
    prev = q(
        "SELECT type, amount FROM transactions "
        "WHERE telegram_id=%s AND EXTRACT(YEAR FROM date)=%s AND EXTRACT(MONTH FROM date)=%s",
        (uid, py, pm),
    )
    prev_exp = sum(float(r["amount"]) for r in prev if r["type"] == "expense") if prev else None

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    with c1: kpi("📥 Daromad",    fmt(income),   color="#3FB950")
    with c2: kpi("📤 Xarajat",    fmt(expenses),
                 delta=((expenses - prev_exp) if prev_exp is not None else None),
                 color="#F85149")
    with c3: kpi("💵 Balans",     fmt(balance),  color="#6C63FF")
    with c4: kpi("🔢 Amaliyotlar", str(len(df)), color="#F39C12")

    # Budget progress
    budget = st.session_state.get("budget", 0)
    if budget > 0:
        pct = min(expenses / budget, 1.0)
        bar_color = "#3FB950" if pct < 0.7 else "#F39C12" if pct < 1.0 else "#F85149"
        st.markdown(
            f"**Budget:** {fmt(expenses)} / {fmt(budget)} "
            f"({int(pct*100)}%)"
        )
        st.progress(pct)

    st.markdown("---")

    # Charts
    cl, cr = st.columns(2)

    with cl:
        sec("📈 Kunlik xarajatlar")
        exp_df = df[df["type"] == "expense"].copy()
        if not exp_df.empty:
            exp_df["d"] = pd.to_datetime(exp_df["date"]).dt.date
            daily = exp_df.groupby("d")["amount"].sum().reset_index()
            daily.columns = ["Sana", "Xarajat"]
            fig = px.area(daily, x="Sana", y="Xarajat",
                          template="plotly_dark", color_discrete_sequence=["#F85149"])
            fig.update_traces(fillcolor="rgba(248,81,73,0.15)", line_width=2)
            fig.update_yaxes(tickformat=",.0f")
            st.plotly_chart(chart_layout(fig), use_container_width=True)
        else:
            st.info("Bu oy xarajatlar yo'q")

    with cr:
        sec("🏆 Kategoriyalar bo'yicha")
        cat_df = df[df["type"] == "expense"].copy()
        if not cat_df.empty:
            cats = cat_df.groupby("category")["amount"].sum().reset_index()
            cats.columns = ["Kategoriya", "Miqdor"]
            cats = cats.sort_values("Miqdor", ascending=False)
            fig2 = px.pie(cats, values="Miqdor", names="Kategoriya",
                          template="plotly_dark", color_discrete_sequence=PALETTE,
                          hole=0.35)
            fig2.update_traces(textposition="inside", textinfo="percent+label",
                               textfont_size=11)
            st.plotly_chart(chart_layout(fig2), use_container_width=True)
        else:
            st.info("Bu oy xarajatlar yo'q")

    # Income vs Expense bar
    st.markdown("---")
    sec("📊 Daromad va xarajat taqqoslash")
    inc_cats = df[df["type"] == "income"].groupby("category")["amount"].sum().reset_index()
    inc_cats.columns = ["Kategoriya", "Miqdor"]
    inc_cats["Tur"] = "Daromad"
    exp_cats = df[df["type"] == "expense"].groupby("category")["amount"].sum().reset_index()
    exp_cats.columns = ["Kategoriya", "Miqdor"]
    exp_cats["Tur"] = "Xarajat"
    combo = pd.concat([inc_cats, exp_cats])
    if not combo.empty:
        fig3 = px.bar(combo, x="Kategoriya", y="Miqdor", color="Tur", barmode="group",
                      template="plotly_dark",
                      color_discrete_map={"Daromad": "#3FB950", "Xarajat": "#F85149"})
        fig3.update_yaxes(tickformat=",.0f")
        st.plotly_chart(chart_layout(fig3, height=280), use_container_width=True)

# ─────────────────────────── Transactions ────────────────────────────
def page_transactions():
    uid = st.session_state["user_id"]
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("## 📋 Tranzaksiyalar")

    # Filters
    c1, c2, c3 = st.columns(3)
    with c1:
        start = st.date_input("📅 Boshlanish", value=date.today().replace(day=1))
    with c2:
        end = st.date_input("📅 Tugash", value=date.today())
    with c3:
        tx_type = st.selectbox("Tur", ["Barchasi", "Daromad", "Xarajat"])

    rows = q(
        "SELECT t.id, t.date, t.type, t.amount, t.category, t.note, b.name AS balance_name "
        "FROM transactions t "
        "LEFT JOIN balances b ON t.balance_id=b.id "
        "WHERE t.telegram_id=%s "
        "  AND DATE(t.date AT TIME ZONE 'Asia/Tashkent') BETWEEN %s AND %s "
        "ORDER BY t.date DESC",
        (uid, start, end),
    )

    type_map = {"Daromad": "income", "Xarajat": "expense"}
    if tx_type in type_map:
        rows = [r for r in rows if r["type"] == type_map[tx_type]]

    if not rows:
        st.info("Tanlangan davr va filtr bo'yicha tranzaksiyalar yo'q")
        return

    df = pd.DataFrame(rows)
    df["amount"] = df["amount"].astype(float)
    inc  = df[df["type"] == "income"]["amount"].sum()
    exp  = df[df["type"] == "expense"]["amount"].sum()

    k1, k2, k3 = st.columns(3)
    with k1: kpi("📥 Daromad",    fmt(inc),        color="#3FB950")
    with k2: kpi("📤 Xarajat",    fmt(exp),        color="#F85149")
    with k3: kpi("💵 Balans",     fmt(inc - exp),  color="#6C63FF")

    # Category filter
    all_cats = sorted(df["category"].dropna().unique().tolist())
    sel_cats = st.multiselect("Kategoriyalar", all_cats, default=all_cats)
    if sel_cats:
        df = df[df["category"].isin(sel_cats)]

    # Display
    disp = df.copy()
    disp["Sana"]       = pd.to_datetime(disp["date"]).dt.strftime("%d.%m.%Y %H:%M")
    disp["Tur"]        = disp["type"].map({"income": "📥 Daromad", "expense": "📤 Xarajat"})
    disp["Miqdor"]     = disp["amount"].apply(fmt)
    disp = disp.rename(columns={
        "category":     "Kategoriya",
        "note":         "Izoh",
        "balance_name": "Balans",
    })

    st.dataframe(
        disp[["Sana", "Tur", "Miqdor", "Kategoriya", "Balans", "Izoh"]],
        use_container_width=True,
        hide_index=True,
    )

    # CSV export
    csv_bytes = disp[["Sana", "Tur", "Miqdor", "Kategoriya", "Balans", "Izoh"]]\
        .to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 CSV yuklab olish",
        data=csv_bytes,
        file_name=f"tranzaksiyalar_{start}_{end}.csv",
        mime="text/csv",
    )

# ─────────────────────────── Debts ───────────────────────────────────
def page_debts():
    uid = st.session_state["user_id"]
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("## 💸 Qarzlar")

    view = st.radio(
        "Ko'rinish",
        ["To'lanmaganlar", "To'langanlar", "Barchasi"],
        horizontal=True,
    )

    sql = (
        "SELECT id, person_name, amount, direction, due_date, is_paid, note, created_at "
        "FROM debts WHERE telegram_id=%s"
    )
    params = [uid]
    if view == "To'lanmaganlar":
        sql += " AND is_paid=FALSE"
    elif view == "To'langanlar":
        sql += " AND is_paid=TRUE"
    sql += " ORDER BY is_paid ASC, due_date ASC NULLS LAST, created_at DESC"

    rows = q(sql, tuple(params))
    if not rows:
        st.info("Qarzlar topilmadi")
        return

    today    = date.today()
    gave     = [r for r in rows if r["direction"] == "gave"]
    took     = [r for r in rows if r["direction"] == "took"]
    unpaid_g = sum(float(r["amount"]) for r in gave if not r["is_paid"])
    unpaid_t = sum(float(r["amount"]) for r in took if not r["is_paid"])

    k1, k2 = st.columns(2)
    with k1: kpi("🔴 Men bergan (qaytarish kerak)", fmt(unpaid_g), color="#F85149")
    with k2: kpi("🟢 Men olgan (men qaytaraman)", fmt(unpaid_t), color="#3FB950")

    st.markdown("---")

    def render_group(debts, title):
        if not debts:
            return
        sec(title)
        for d in debts:
            with st.container():
                due   = d.get("due_date")
                paid  = d["is_paid"]
                c1, c2, c3, c4 = st.columns([3, 2, 2, 2])

                c1.markdown(f"**{d['person_name']}**")
                c2.markdown(fmt(float(d["amount"])))

                # Due date + warning
                if due:
                    days_left = (due - today).days if not paid else None
                    due_str = due.strftime("%d.%m.%Y")
                    if not paid and days_left is not None:
                        if days_left < 0:
                            c3.markdown(
                                f"🚨 {due_str} "
                                f'<span class="tag-red">{abs(days_left)} kun kechikdi</span>',
                                unsafe_allow_html=True,
                            )
                        elif days_left <= 7:
                            c3.markdown(
                                f"⚠️ {due_str} "
                                f'<span class="tag-red">{days_left} kun</span>',
                                unsafe_allow_html=True,
                            )
                        else:
                            c3.markdown(f"📅 {due_str}")
                    else:
                        c3.markdown(f"📅 {due_str}")
                else:
                    c3.markdown("—")

                c4.markdown("✅ To'langan" if paid else "🔴 Kutilmoqda")

                if d.get("note"):
                    st.caption(f"📝 {d['note']}")
                st.divider()

    col1, col2 = st.columns(2)
    with col1:
        render_group(gave, "🔴 Men berganlar")
    with col2:
        render_group(took, "🟢 Men olganlar")

# ─────────────────────────── Balances ────────────────────────────────
def page_balances():
    uid = st.session_state["user_id"]
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("## 💳 Balanslar")

    rows = q(
        "SELECT name, type, amount FROM balances WHERE telegram_id=%s ORDER BY created_at",
        (uid,),
    )
    if not rows:
        st.info("Hali balans qo'shilmagan. Botdan balans yarating.")
        return

    total = sum(float(r["amount"]) for r in rows)
    kpi("💰 Umumiy balans", fmt(total), color="#6C63FF")
    st.markdown("---")

    # Balance cards
    n = len(rows)
    cols = st.columns(min(n, 3))
    for i, b in enumerate(rows):
        with cols[i % 3]:
            emoji, color = BALANCE_TYPES.get(b["type"], ("📦", "#95A5A6"))
            kpi(f"{emoji} {b['name']}", fmt(float(b["amount"])), color=color)

    # Pie chart
    if n > 1:
        st.markdown("---")
        sec("Taqsimlash")
        df = pd.DataFrame(rows)
        df["amount"] = df["amount"].astype(float)
        df["emoji"]  = df["type"].apply(lambda t: BALANCE_TYPES.get(t, ("📦", ""))[0])
        df["label"]  = df["emoji"] + " " + df["name"]
        colors = [BALANCE_TYPES.get(t, ("📦", "#95A5A6"))[1] for t in df["type"]]
        fig = px.pie(df, values="amount", names="label",
                     template="plotly_dark", color_discrete_sequence=colors, hole=0.4)
        fig.update_traces(textposition="inside", textinfo="percent+label")
        st.plotly_chart(chart_layout(fig, height=300), use_container_width=True)

    # Transactions per balance (last 30 days)
    st.markdown("---")
    sec("So'nggi 30 kun — har bir balans bo'yicha xarajat")
    bal_exp = q(
        "SELECT b.name, SUM(t.amount) AS total "
        "FROM transactions t "
        "JOIN balances b ON t.balance_id=b.id "
        "WHERE t.telegram_id=%s AND t.type='expense' AND t.date >= NOW()-INTERVAL '30 days' "
        "GROUP BY b.name ORDER BY total DESC",
        (uid,),
    )
    if bal_exp:
        bdf = pd.DataFrame(bal_exp)
        bdf["total"] = bdf["total"].astype(float)
        fig2 = px.bar(bdf, x="name", y="total",
                      template="plotly_dark", color_discrete_sequence=["#F85149"])
        fig2.update_yaxes(tickformat=",.0f")
        fig2.update_layout(xaxis_title="Balans", yaxis_title="Xarajat")
        st.plotly_chart(chart_layout(fig2, height=250), use_container_width=True)
    else:
        st.info("So'nggi 30 kunda balanslarga bog'liq xarajatlar yo'q")

# ─────────────────────────── Admin ───────────────────────────────────
def page_admin():
    if not st.session_state.get("is_admin"):
        st.error("❌ Ruxsat yo'q — faqat admin uchun")
        return

    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("## 👑 Admin Panel")

    # Top stats
    s = q1(
        "SELECT "
        "  COUNT(*) AS total, "
        "  COUNT(*) FILTER(WHERE is_premium AND premium_until > NOW()) AS paid, "
        "  COUNT(*) FILTER("
        "    WHERE NOT is_premium AND registered_at + INTERVAL '7 days' > NOW()"
        "  ) AS trial "
        "FROM users"
    )
    today_act = q1(
        "SELECT COUNT(DISTINCT telegram_id) AS n FROM transactions "
        "WHERE DATE(date AT TIME ZONE 'Asia/Tashkent') = CURRENT_DATE"
    )
    week_act = q1(
        "SELECT COUNT(DISTINCT telegram_id) AS n FROM transactions "
        "WHERE date >= NOW() - INTERVAL '7 days'"
    )
    total_tx = q1("SELECT COUNT(*) AS n FROM transactions")
    total_debt = q1("SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS vol FROM debts WHERE is_paid=FALSE")

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: kpi("👥 Foydalanuvchilar", str(s.get("total", 0)),  color="#6C63FF")
    with c2: kpi("💎 Premium",          str(s.get("paid",  0)),  color="#F39C12")
    with c3: kpi("🆓 Sinov davri",      str(s.get("trial", 0)),  color="#3FB950")
    with c4: kpi("⚡ Bugun faol",        str(today_act.get("n",0)), color="#F85149")
    with c5: kpi("📅 Hafta faol",        str(week_act.get("n",0)), color="#3498DB")

    c6, c7 = st.columns(2)
    with c6: kpi("📊 Jami amaliyotlar", str(total_tx.get("n", 0)),    color="#9B59B6")
    with c7: kpi("💸 Ochiq qarzlar",    str(total_debt.get("n", 0)),  color="#E67E22")

    st.markdown("---")

    # Charts row 1
    cl, cr = st.columns(2)

    with cl:
        sec("📈 Yangi foydalanuvchilar (30 kun)")
        ur = q(
            "SELECT DATE(registered_at AT TIME ZONE 'Asia/Tashkent') AS dt, COUNT(*) AS n "
            "FROM users WHERE registered_at >= NOW() - INTERVAL '30 days' "
            "GROUP BY dt ORDER BY dt"
        )
        if ur:
            udf = pd.DataFrame(ur)
            fig = px.bar(udf, x="dt", y="n", template="plotly_dark",
                         color_discrete_sequence=["#6C63FF"],
                         labels={"dt": "Sana", "n": "Foydalanuvchilar"})
            st.plotly_chart(chart_layout(fig), use_container_width=True)
        else:
            st.info("Ma'lumot yo'q")

    with cr:
        sec("📊 Amaliyotlar dinamikasi (30 kun)")
        tr = q(
            "SELECT DATE(date AT TIME ZONE 'Asia/Tashkent') AS dt, COUNT(*) AS n "
            "FROM transactions WHERE date >= NOW() - INTERVAL '30 days' "
            "GROUP BY dt ORDER BY dt"
        )
        if tr:
            tdf = pd.DataFrame(tr)
            fig2 = px.area(tdf, x="dt", y="n", template="plotly_dark",
                           color_discrete_sequence=["#2ECC71"],
                           labels={"dt": "Sana", "n": "Amaliyotlar"})
            fig2.update_traces(fillcolor="rgba(46,204,113,0.15)")
            st.plotly_chart(chart_layout(fig2), use_container_width=True)
        else:
            st.info("Ma'lumot yo'q")

    # Charts row 2
    cl2, cr2 = st.columns(2)

    with cl2:
        sec("💎 Premium taqsimoti")
        total = s.get("total") or 1
        paid  = s.get("paid",  0)
        trial = s.get("trial", 0)
        other = max(total - paid - trial, 0)
        fig3 = go.Figure(go.Bar(
            x=["Jami", "Premium", "Sinov", "Boshqa"],
            y=[total, paid, trial, other],
            marker_color=["#6C63FF", "#F39C12", "#3FB950", "#95A5A6"],
            text=[str(v) for v in [total, paid, trial, other]],
            textposition="auto",
        ))
        fig3.update_layout(showlegend=False)
        st.plotly_chart(chart_layout(fig3, height=260), use_container_width=True)

    with cr2:
        sec("📋 Eng ko'p kategoriyalar (barcha foydalanuvchilar)")
        cr_data = q(
            "SELECT category, COUNT(*) AS cnt FROM transactions "
            "WHERE type='expense' GROUP BY category ORDER BY cnt DESC LIMIT 10"
        )
        if cr_data:
            cdf = pd.DataFrame(cr_data)
            fig4 = px.bar(cdf, x="cnt", y="category", orientation="h",
                          template="plotly_dark",
                          color_discrete_sequence=["#F85149"],
                          labels={"cnt": "Soni", "category": ""})
            fig4.update_layout(yaxis=dict(autorange="reversed"))
            st.plotly_chart(chart_layout(fig4, height=260), use_container_width=True)
        else:
            st.info("Ma'lumot yo'q")

    # Recent activity (NO personal data — just aggregates)
    st.markdown("---")
    sec("🕐 Bugungi faollik (agregat)")
    today_rows = q(
        "SELECT type, COUNT(*) AS cnt, SUM(amount) AS vol "
        "FROM transactions "
        "WHERE DATE(date AT TIME ZONE 'Asia/Tashkent') = CURRENT_DATE "
        "GROUP BY type"
    )
    if today_rows:
        for r in today_rows:
            label = "📥 Daromad" if r["type"] == "income" else "📤 Xarajat"
            st.markdown(
                f"**{label}:** {r['cnt']} ta amaliyot — jami {fmt(r['vol'])}"
            )
    else:
        st.info("Bugun hali amaliyotlar yo'q")

# ─────────────────────────── Main ────────────────────────────────────
def main():
    if not DATABASE_URL:
        st.error(
            "⚠️ **DATABASE_URL** muhit o'zgaruvchisi sozlanmagan.\n\n"
            "Render.com'da Environment → Add Environment Variable'dan qo'shing."
        )
        return

    if "user_id" not in st.session_state:
        page_login()
        return

    page = render_sidebar()

    if page == "📊 Umumiy":
        page_overview()
    elif page == "📋 Tranzaksiyalar":
        page_transactions()
    elif page == "💸 Qarzlar":
        page_debts()
    elif page == "💳 Balanslar":
        page_balances()
    elif page == "👑 Admin Panel":
        page_admin()

main()
