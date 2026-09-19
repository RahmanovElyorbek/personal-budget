#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -f "venv/bin/activate" ]; then
    echo "Avval ./setup.sh ni ishga tushiring!"
    exit 1
fi

source venv/bin/activate

if [ ! -f ".env" ]; then
    echo ".env fayli topilmadi! .env.example dan nusxa oling va to'ldiring."
    exit 1
fi

if [ ! -f "credentials.json" ]; then
    echo "credentials.json topilmadi! Google Service Account faylini joylashtiring."
    exit 1
fi

echo "Bot ishga tushmoqda..."
python bot.py
