#!/usr/bin/env bash
set -e

echo "=== Ishchilar Boti — O'rnatish ==="

# Python versiyasini tekshirish
python3 --version >/dev/null 2>&1 || { echo "Python3 topilmadi!"; exit 1; }

# Virtual muhit
if [ ! -d "venv" ]; then
    echo "Virtual muhit yaratilmoqda..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "Kutubxonalar o'rnatilmoqda..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# .env fayl
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "✅ .env fayli yaratildi. Iltimos quyidagilarni to'ldiring:"
    echo "   BOT_TOKEN      — BotFather dan olgan token"
    echo "   ADMIN_ID       — Sizning Telegram ID ingiz"
    echo "   SPREADSHEET_ID — Google Sheets ID si"
    echo ""
    echo "   Keyin botni ishga tushirish uchun: ./run.sh"
else
    echo "✅ .env fayli allaqachon mavjud."
    echo "   Botni ishga tushirish: ./run.sh"
fi
