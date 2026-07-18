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

## Deploy on Render.com

1. Go to https://dashboard.render.com → **New** → **Web Service**
2. Connect your GitHub repo (`rahmanovelyorbek/personal-budget`)
3. Set the following fields:

| Field | Value |
|---|---|
| **Name** | `oson-budget-dashboard` (or any name) |
| **Root Directory** | `dashboard` |
| **Runtime** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `streamlit run app.py --server.port $PORT --server.address 0.0.0.0` |

4. Under **Environment** → **Add Environment Variable**, add:
   - `DATABASE_URL` = your Supabase connection string
   - `ADMIN_ID` = your Telegram ID

5. Click **Deploy**. After deploy completes, copy the service URL (e.g. `https://oson-budget-dashboard.onrender.com`).

6. Set `DASHBOARD_URL` in the **bot's** Render service environment to this URL so the "🌐 Web-kabinet" button sends the correct link.

## Login Flow

1. In the Telegram bot tap **🌐 Web-kabinet**
2. The bot displays your Telegram ID and a 6-digit one-time code (valid 10 minutes)
3. Open the dashboard URL, enter your Telegram ID and the code
4. You are logged in — the code becomes invalid immediately after use
