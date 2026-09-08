# Ishchilar Telegram Boti

Telegram bot — davomat belgilash, yangiliklar va ishchi ro'yxati, barchasi Google Sheets bilan sinxronlashadi.

## Imkoniyatlar

| | Funksiya | Tavsif |
|---|---|---|
| ✅ | Keldi | Ishchi ishga kelganini belgilaydi → Sheetsga saqlanadi |
| 🚪 | Ketdi | Ishchi ishdan ketganini belgilaydi → Sheetsga saqlanadi |
| 📝 | Ro'yxatdan o'tish | Yangi ishchi ism/familiya/lavozim/telefon kiritadi → Sheetsga saqlanadi |
| 📢 | Broadcast | Admin barcha ishchilarga xabar yuboradi |
| 👥 | Ishchilar | Admin ishchilar ro'yxatini ko'radi |
| 📊 | Bugungi davomat | Admin bugun kim keldi/ketdi — barchasini bir joyda ko'radi |

---

## O'rnatish

### 1-qadam — Bot token olish

1. Telegramda [@BotFather](https://t.me/BotFather) ni oching
2. `/newbot` yuboring, bot nomini kiriting
3. Tokenni nusxalab saqlang (keyinroq kerak bo'ladi)

### 2-qadam — Google tayyorlash

1. [Google Cloud Console](https://console.cloud.google.com/) ga kiring
2. Yangi loyiha yarating (yoki mavjudini tanlang)
3. **APIs & Services → Enable APIs** orqali yoqing:
   - **Google Sheets API**
   - **Google Drive API**
4. **APIs & Services → Credentials → Create Credentials → Service Account** yarating
5. Service account → **Keys → Add Key → JSON** — faylni yuklab, `credentials.json` nomi bilan bot papkasiga saqlang
6. [Google Sheets](https://sheets.google.com/) da yangi jadval yarating
7. Jadval URL dan ID ni oling:
   ```
   https://docs.google.com/spreadsheets/d/  →SHUBU←  /edit
   ```
8. Jadvalni service account emaili bilan ulashing (**Editor** huquqi bilan):
   - Jadval → Share → service account emailini kiriting (masalan: `bot@project.iam.gserviceaccount.com`)

### 3-qadam — Admin ID ni bilish

[@userinfobot](https://t.me/userinfobot) ga `/start` yuboring — u sizning ID ingizni ko'rsatadi.

### 4-qadam — Fayllarni sozlash

```bash
cd employee_bot

# O'rnatish
bash setup.sh

# .env faylini oching va to'ldiring
nano .env
```

`.env` ichida:
```
BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_ID=123456789
SPREADSHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
GOOGLE_CREDENTIALS_FILE=credentials.json
```

### 5-qadam — Ishga tushirish

```bash
bash run.sh
```

---

## Google Sheets tuzilmasi

Bot avtomatik ravishda 2 ta varaq yaratadi:

**Davomat** varag'i:
| Telegram ID | Ism Familiya | Holat | Sana | Vaqt |
|---|---|---|---|---|
| 123456789 | Ali Valiyev | Keldi | 2025-01-15 | 09:02:34 |
| 123456789 | Ali Valiyev | Ketdi | 2025-01-15 | 18:05:11 |

**Ishchilar** varag'i:
| Telegram ID | Ism | Familiya | Lavozim | Telefon | Ro'yxatdan o'tgan sana |
|---|---|---|---|---|---|
| 123456789 | Ali | Valiyev | Dasturchi | +998901234567 | 2025-01-10 |

---

## Bot buyruqlari

| Buyruq | Kim uchun |
|---|---|
| `/start` | Hammaga |
| `/royxat` | Yangi ishchilar |
| `/keldi` | Ishchilar |
| `/ketdi` | Ishchilar |
| `/broadcast` | Faqat Admin |
| `/ishchilar` | Faqat Admin |
| `/davomat` | Faqat Admin |
| `/cancel` | Amaliyotni bekor qilish |

---

## Server da doimiy ishlatish (Linux)

`/etc/systemd/system/employee-bot.service`:

```ini
[Unit]
Description=Employee Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/employee_bot
ExecStart=/home/ubuntu/employee_bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable employee-bot
sudo systemctl start employee-bot
sudo systemctl status employee-bot
```
