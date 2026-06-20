# 🤖 Ishchilar Telegram Boti

Ishchilar uchun Telegram bot — davomat belgilash, yangiliklar va ro'yxatdan o'tish.

## Imkoniyatlar

| Funksiya | Tavsif |
|---|---|
| ✅ Keldi | Ishga kelganda belgilash — Google Sheetsga saqlanadi |
| 🚪 Ketdi | Ishdan ketganda belgilash — Google Sheetsga saqlanadi |
| 📝 Ro'yxatdan o'tish | Yangi ishchi ma'lumotlarini kiritish (ism, lavozim, telefon) |
| 📢 Broadcast | Admin barcha ishchilarga xabar yuboradi |
| 👥 Ishchilar ro'yxati | Admin uchun: barcha ishchilarni ko'rish |

## O'rnatish

### 1. Telegram Bot yaratish

1. [@BotFather](https://t.me/BotFather) ga o'ting
2. `/newbot` buyrug'ini yuboring
3. Bot nomini kiriting
4. Token ni nusxalab oling

### 2. Google Sheets va Service Account

1. [Google Cloud Console](https://console.cloud.google.com/) ga kiring
2. Yangi loyiha yarating yoki mavjudini tanlang
3. **APIs & Services → Enable APIs** ga o'ting:
   - Google Sheets API
   - Google Drive API
4. **APIs & Services → Credentials → Create Credentials → Service Account** yarating
5. Service Account uchun JSON kalit yarating va `credentials.json` nomi bilan bot papkasiga saqlang
6. [Google Sheets](https://sheets.google.com/) da yangi jadval yarating
7. Jadval URL dan ID ni oling: `https://docs.google.com/spreadsheets/d/**SPREADSHEET_ID**/edit`
8. Jadvalni service account emaili bilan ulashing (Editor huquqi bilan)

### 3. Muhit o'zgaruvchilarini sozlash

```bash
cp .env.example .env
```

`.env` faylini tahrirlang:

```env
BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxx
ADMIN_ID=123456789
SPREADSHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
GOOGLE_CREDENTIALS_FILE=credentials.json
```

> **ADMIN_ID** ni bilish uchun [@userinfobot](https://t.me/userinfobot) ga `/start` yuboring.

### 4. Python muhitini sozlash

```bash
cd employee_bot
python -m venv venv
source venv/bin/activate        # Linux/Mac
# yoki
venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### 5. Botni ishga tushirish

```bash
python bot.py
```

## Google Sheets tuzilmasi

Bot avtomatik ravishda 2 ta varaq (sheet) yaratadi:

### 📋 Davomat varag'i
| Telegram ID | Ism Familiya | Holat | Sana | Vaqt |
|---|---|---|---|---|
| 123456789 | Ali Valiyev | Keldi | 2025-01-15 | 09:02:34 |
| 123456789 | Ali Valiyev | Ketdi | 2025-01-15 | 18:05:11 |

### 👥 Ishchilar varag'i
| Telegram ID | Ism | Familiya | Lavozim | Telefon | Ro'yxatdan o'tgan sana |
|---|---|---|---|---|---|
| 123456789 | Ali | Valiyev | Dasturchi | +998901234567 | 2025-01-10 |

## Bot buyruqlari

| Buyruq | Kim uchun | Tavsif |
|---|---|---|
| `/start` | Hammasi | Botni ishga tushirish |
| `/keldi` | Ishchilar | Ishga kelganini belgilash |
| `/ketdi` | Ishchilar | Ishdan ketganini belgilash |
| `/royxat` | Yangi ishchilar | Ro'yxatdan o'tish |
| `/broadcast` | Faqat Admin | Barcha ishchilarga xabar yuborish |
| `/ishchilar` | Faqat Admin | Ishchilar ro'yxatini ko'rish |

## Server da ishlatish (ixtiyoriy)

### systemd service (Linux)

`/etc/systemd/system/employee-bot.service` faylini yarating:

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
sudo systemctl enable employee-bot
sudo systemctl start employee-bot
sudo systemctl status employee-bot
```
