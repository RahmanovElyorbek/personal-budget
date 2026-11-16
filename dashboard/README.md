# Oson Budget — Web Dashboard

Streamlit-based web cabinet for premium users and admin panel.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | PostgreSQL connection string (same as bot) |
| `ADMIN_ID` | Yes | Your Telegram user ID (integer) — grants admin panel access |

Example:
```
DATABASE_URL=postgresql://user:password@host:5432/dbname
ADMIN_ID=123456789
```

## Local Test (Windows)

```cmd
cd dashboard
pip install -r requirements.txt
set DATABASE_URL=postgresql://user:password@host:5432/dbname
set ADMIN_ID=123456789
streamlit run app.py
```

Then open http://localhost:8501 in the browser.

## Deploy on Railway

1. Railway project (the same one as the bot) → **+ Create** → **GitHub Repo** → `rahmanovelyorbek/personal-budget`
2. Service **Settings**:

| Field | Value |
|---|---|
| **Root Directory** | `/dashboard` |
| **Config file path** | `/dashboard/railway.json` |

   Start command and healthcheck (`/_stcore/health`) come from `dashboard/railway.json`.

3. **Variables**: `DATABASE_URL` (Supabase connection string), `ADMIN_ID` (your Telegram ID)
4. **Settings → Networking → Generate Domain**, copy the URL.
5. Set `DASHBOARD_URL` in the **bot's** Railway service variables to this URL so the "🌐 Web-kabinet" button sends the correct link.

Full migration guide (Uzbek): [`docs/RAILWAY.md`](../docs/RAILWAY.md)

## Login Flow

1. In the Telegram bot tap **🌐 Web-kabinet**
2. The bot displays your Telegram ID and a 6-digit one-time code (valid 10 minutes)
3. Open the dashboard URL, enter your Telegram ID and the code
4. You are logged in — the code becomes invalid immediately after use
