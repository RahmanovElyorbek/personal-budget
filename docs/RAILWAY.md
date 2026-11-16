# Oson Budget — Render'dan Railway'ga ko'chirish

Baza (Supabase) **o'zgarmaydi** — faqat bot va web-kabinet serverlari ko'chadi.
Shuning uchun foydalanuvchi ma'lumotlari yo'qolmaydi, migratsiya kerak emas.

Repo'da tayyor turgan fayllar:

| Fayl | Vazifasi |
|---|---|
| `railway.json` | Bot servisi: start buyrug'i, healthcheck `/`, 1 ta replica, qulasa qayta ishga tushirish |
| `dashboard/railway.json` | Web-kabinet (Streamlit): healthcheck `/_stcore/health` |
| `.python-version`, `dashboard/.python-version` | Python 3.11 (eng oxirgi patch) |

Kodda: `WEBHOOK_URL` berilmasa, bot Railway'ning `RAILWAY_PUBLIC_DOMAIN`
o'zgaruvchisidan manzilni o'zi oladi.

---

## 0-qadam. Render'dagi o'zgaruvchilarni nusxalab oling

Render → `personal-budget` servisi → **Environment** → hammasini bloknotga
ko'chiring (keyin Railway'ga qo'yiladi):

- `BOT_TOKEN`
- `DATABASE_URL` (Supabase)
- `OPENAI_API_KEY`
- `ADMIN_ID`
- `DASHBOARD_URL` (bo'lsa)
- `GUIDE_VIDEO_FILE_ID` (bo'lsa)
- boshqa qo'shgan o'zgaruvchilaringiz

> ⚠️ `DATABASE_URL` ni **aynan Render'dagidek** qoldiring. Agar Supabase'ning
> "Direct connection" (`db.xxxx.supabase.co:5432`) manzili ulanmasa —
> Supabase → Project Settings → Database → **Connection pooler (Session mode)**
> manzilini oling (IPv4 orqali ishlaydi).

## 1-qadam. Bot servisini yaratish

1. https://railway.com → **New Project** → **Deploy from GitHub repo**
2. `RahmanovElyorbek/personal-budget` ni tanlang
   (branch: `main` — avval shu o'zgarishlar main'ga merge qilingan bo'lsin).
3. Servis ochilgach → **Variables** → **Raw Editor** → 0-qadamdagi
   o'zgaruvchilarni `KEY=value` ko'rinishida joylang.
   `PORT` ni **qo'ymang** — Railway o'zi beradi.
   `WEBHOOK_URL` ni ham qo'ymasangiz bo'ladi (pastga qarang).
4. **Settings → Networking → Generate Domain** — masalan
   `personal-budget-production.up.railway.app` chiqadi.
5. **Deploy** tugmasini bosing (yoki avtomatik boshlanadi).

## 2-qadam. Ishlayotganini tekshirish

**Deployments → View logs** da quyidagilar chiqishi kerak:

```
🚀 Port 8080 ochildi — bot ishga tushmoqda...
✅ [2/5] DB tayyor!
✅ [4/5] Telegram tayyor!
✅ [5/5] Webhook set: https://....up.railway.app/webhook/...
🎉 Bot to'liq ishga tushdi!
```

Brauzerda domenni oching → `✅ Oson Byudjet Bot is alive!` chiqsa — joyida.
Telegram'da @monthbudget_bot ga `/start` yozib, xarajat kiritib ko'ring.

## 3-qadam. Render'ni TO'XTATISH (majburiy!)

Render → bot servisi → **Settings → Suspend Web Service**.

Nega muhim:
- Render servisi uyg'onganda webhook'ni **yana o'ziga** qaytarib oladi —
  bot goh Render'da, goh Railway'da ishlab, xabarlar yo'qoladi.
- Ikkala server ham eslatma (scheduler) yuboradi → foydalanuvchilarga
  har bir eslatma **2 marta** boradi.

Bir-ikki kun hammasi joyida ishlasa, Render servisini butunlay o'chirsangiz bo'ladi.

## 4-qadam. Web-kabinet (dashboard) — agar ishlatsangiz

1. Shu Railway loyihasida → **+ Create → GitHub Repo** → yana shu repo.
2. Yangi servis → **Settings**:
   - **Root Directory**: `/dashboard`
   - **Config file path** (Config-as-code): `/dashboard/railway.json`
3. **Variables**: `DATABASE_URL`, `ADMIN_ID` (botdagi bilan bir xil).
4. **Networking → Generate Domain**.
5. Shu domenni **bot servisi**ning Variables'ida `DASHBOARD_URL` ga qo'ying
   (bot avtomatik qayta deploy bo'ladi).
6. Render'dagi dashboard servisini ham **Suspend** qiling.

## 5-qadam. MCP (Claude/ChatGPT ulanishi) manzilini yangilash

MCP manzili domen bilan birga o'zgaradi:

```
eski:  https://personal-budget-6mr0.onrender.com/mcp
yangi: https://<railway-domen>/mcp
```

- Botda `/mcp_ulash` endi avtomatik yangi manzilni beradi.
- Claude'ga AI ulagan foydalanuvchilar connector'ni **yangi manzil bilan
  qayta qo'shishi** kerak (token'lar bazada — o'zi saqlanadi, faqat URL o'zgaradi).
- `docs/MCP.md` dagi manzillarni yangi domenga almashtiring.

> 💡 Keyinchalik domen yana o'zgarmasligi uchun o'z domeningizni ulash mumkin
> (masalan `api.osonbudget.uz`): Railway → Settings → Networking → Custom Domain.

## Narx haqida

Railway'da bepul "uxlash" rejimi yo'q — Hobby plan ($5/oy, shu summa
ichida resurs ishlatiladi). Bot doim uyg'oq turadi, shuning uchun
keep-alive / ping xizmatlari (UptimeRobot va h.k.) endi **kerak emas**.

## Muammo bo'lsa

| Belgi | Sabab / yechim |
|---|---|
| `DB ulanishi 45 soniyada timeout` takrorlanadi | `DATABASE_URL` ni Supabase **pooler** manziliga almashtiring |
| `Webhook set: /webhook/...` (domensiz) | Networking → Generate Domain qilinmagan |
| Healthcheck failed | Logs'da Python xatosini qidiring; `PORT` qo'lda qo'yilmaganini tekshiring |
| Eslatmalar 2 marta kelyapti | Render servisi hali ishlayapti — Suspend qiling |
