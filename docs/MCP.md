# Oson Byudjet — MCP Server

Bot bazasini AI (Claude va boshqa MCP mijozlar) bilan bevosita ulaydigan
Model Context Protocol serveri. Manzil: `https://personal-budget-6mr0.onrender.com/mcp`.

32 ta tool: tranzaksiyalar, statistika, qarzlar, kategoriyalar, sozlamalar,
PDF hisobot. Barchasi premium obuna talab qiladi (`whoami` va `get_profile`
bundan mustasno).

---

## 1. Ulanish

Ikki xil ulanish usuli bor — ikkalasi ham bir vaqtda ishlaydi.

### A) OAuth (tavsiya etiladi — "bitta manzil joylashtirib ulash")

Claude ulanish oynasida faqat manzilni kiriting, qolganini Claude o'zi
so'raydi:

```
https://personal-budget-6mr0.onrender.com/mcp
```

Claude sizni brauzerda ulash sahifasiga yo'naltiradi. O'sha sahifada:

1. Telegram botga `/mcp_login` yuboring — 10 daqiqalik bir martalik kod
   olasiz.
2. Kodni sahifaga kiriting → ulanish tayyor.

Token fon rejimida avtomatik yangilanadi (refresh token 180 kun amal
qiladi) — qo'lda hech narsa qilish shart emas.

**Bir nechta Telegram hisobini ulash:** Claude bitta tashkilotda bir xil
connector URL'ini ikkinchi marta qo'shishga yo'l qo'ymaydi. Ikkinchi
hisob uchun manzilga ixtiyoriy raqam qo'shing:

```
https://personal-budget-6mr0.onrender.com/mcp/2
```

(`2` — shunchaki Claude uchun boshqa URL sifatida ko'rinishi uchun, hech
qanday maxsus ma'noga ega emas — qaysi Telegram hisobiga ulanish
`/mcp_login` kodi orqali aniqlanadi, URL'dagi raqam orqali emas.)

### B) Doimiy token (Claude Desktop config uchun qulay)

Telegram botga `/mcp_ulash [nom]` yuboring — token va tayyor config bloki
keladi:

```json
{
  "mcpServers": {
    "oson-byudjet": {
      "url": "https://personal-budget-6mr0.onrender.com/mcp",
      "headers": { "Authorization": "Bearer TOKEN_BU_YERGA" }
    }
  }
}
```

Bu faylni Claude Desktop'ning `claude_desktop_config.json` fayliga
qo'shing va Claude Desktop'ni qayta ishga tushiring.

Token **muddatsiz** — faqat qo'lda bekor qilinadi:

- `/mcp_royxat` — faol tokenlar ro'yxati (label + oxirgi ishlatilgan sana)
- `/mcp_ochirish <nom>` — tokenni bekor qilish

Ikkinchi (uchinchi, ...) hisob uchun — o'sha hisobdan botga
`/mcp_ulash Ikkinchi akkaunt` yuboring, config'ga yana bitta server
qo'shing (nomi boshqacha, `url` bir xil, `Authorization` boshqa token).

---

## 2. Tool katalogi (32 ta)

### Fundament

| Tool | Premium? | Nima qiladi |
|---|---|---|
| `whoami` | Yo'q | Token qaysi user'ga tegishli, premium holati, ruxsatlar |
| `get_profile` | Yo'q | Ism, valyuta (UZS), tz (Asia/Tashkent), oylik budjet |
| `list_categories` | Ha | Barcha kategoriyalar (tizim + shaxsiy), qidiruv bilan, sahifalangan |
| `get_used_categories` | Ha | Eng ko'p ishlatilgan kategoriyalar — `add_transaction`dan oldin chaqirish uchun |
| `list_subcategories` | Ha | Berilgan kategoriyaning bolalari |

### Tranzaksiya CRUD

| Tool | Premium? | Nima qiladi |
|---|---|---|
| `add_transaction` | Ha | Yangi kirim/chiqim (v2: `category_id` tercih qilinadi, eski `category` matni 1 oy backward-compat) |
| `get_transaction` | Ha | Bitta tranzaksiyaning to'liq maydonlari |
| `update_transaction` | Ha | Faqat berilgan maydonlarni yangilaydi (balans/hisob o'zgarmaydi) |
| `delete_transaction` | Ha | Soft-delete (balans effekti qaytariladi, yozuv fizik o'chmaydi) |
| `replace_transaction` | Ha | Atomik almashtirish (hisob/balans o'zgarganda) |
| `list_transactions` | Ha | Oxirgi yozuvlar, kunlar bo'yicha guruhlangan |
| `get_reports` | Ha | Filtrlangan (sana/tur/kategoriya/qidiruv) xom ro'yxat, sahifalangan |

### Statistika (SQL agregatsiya, Python loop emas)

| Tool | Premium? | Nima qiladi |
|---|---|---|
| `get_summary` | Ha | Davr bo'yicha kirim/chiqim/balans + o'tgan davr bilan taqqoslash (%) |
| `get_spending_overview` | Ha | Davr bo'yicha jami + TO'LIQ kategoriya taqsimoti |
| `get_category_stats` | Ha | Xuddi shu, lekin ona→subkategoriya daraxti ko'rinishida |
| `get_balance_timeseries` | Ha | Kunlik/haftalik/oylik balans qatori (grafik uchun) |
| `compare_periods` | Ha | Ikki davrni kategoriya kesimida solishtiradi (Hisobchi AI'da yo'q — ustunlik) |

### Qarzlar

| Tool | Premium? | Nima qiladi |
|---|---|---|
| `get_debts_summary` | Ha | FAQAT umumiy jami — hech qanday shaxsiy summa yo'q |
| `get_debts_detail` | Ha | Odam kesimida guruhlangan, har kishining barcha qarzlari |
| `add_debt` | Ha | Yangi qarz (`direction`: `gave`/`took`) |
| `return_debt` | Ha | To'liq yopish |
| `partial_return_debt` | Ha | Qisman yopish (ortiqcha to'lov avtomatik to'liq yopadi) |
| `list_closed_debts` | Ha | Yopilgan qarzlar tarixi |

### Kategoriya boshqaruvi

| Tool | Premium? | Nima qiladi |
|---|---|---|
| `create_category` | Ha | Yangi shaxsiy kategoriya (`parent_id` bersangiz — subkategoriya) |
| `update_category` | Ha | FAQAT shaxsiy kategoriya nomi/emoji/rangini o'zgartiradi |
| `delete_category` | Ha | Shaxsiy — o'chiriladi; tizim kategoriyasi — faqat siz uchun berkitiladi |

### Sozlamalar va PDF

| Tool | Premium? | Nima qiladi |
|---|---|---|
| `set_budget` | Ha | Oylik umumiy budjet limiti |
| `get_notification_settings` | Ha | Kunlik eslatma holati |
| `update_notification_settings` | Ha | Eslatmani yoqish/o'chirish/vaqtini o'zgartirish (18/20/22) |
| `generate_pdf_report` | Ha | PDF hisobot + 24 soatlik yuklab olish havolasi |

### Legacy (v1, orqaga moslik uchun saqlangan)

| Tool | Izoh |
|---|---|
| `get_transactions` | Eski (parametrsiz/oddiy sana oralig'i) tranzaksiya ro'yxati — yangi integratsiyalar `list_transactions`/`get_reports` ishlatsin |
| `get_debts` | Eski xom qarzlar ro'yxati — yangi integratsiyalar `get_debts_detail` ishlatsin |

---

## 3. Savol → Tool xaritasi

| Foydalanuvchi savoli | Chaqiriladigan tool |
|---|---|
| "Bugun 45 ming taksiga ketdi" | `get_used_categories` (kategoriya tanlash) → `add_transaction` |
| "Shu oy qancha sarfladim?" | `get_summary` |
| "Eng ko'p qaysi kategoriyaga ketyapti?" | `get_spending_overview` |
| "O'tgan oy bilan solishtir" | `compare_periods` |
| "Oxirgi yozuvni o'chir" | `list_transactions` → `delete_transaction` (tasdiqdan keyin) |
| "Kechagi taksi 45 emas 55 ming edi" | `list_transactions`/`get_reports` → `update_transaction` |
| "Kimlarga qarzim bor?" | `get_debts_detail` |
| "Aliga qancha qarzim bor?" | `get_debts_detail` (HECH QACHON `get_debts_summary` emas) |
| "Alining qarzidan 200 ming qaytardi" | `get_debts_detail` → `partial_return_debt` |
| "Shu oy uchun PDF hisobot tayyorla" | `generate_pdf_report` |
| "Oxirgi 3 oyda pulim qanday o'zgardi" | `get_balance_timeseries` |
| "Transport ichida taksi qancha" | `get_category_stats` |
| "Yangi kategoriya qo'sh: Uy hayvonlari" | `create_category` |
| "Eslatmani o'chir" | `update_notification_settings` |

---

## 4. Xatolar formati

```json
{"error": "code", "message": "O'zbekcha tushuntirish", "hint": "AI uchun nima qilish kerakligi"}
```

Kodlar: `not_found`, `validation_error`, `premium_required`, `rate_limited`,
`unauthorized`, `internal_error`.

`get_transaction`/`update_transaction`/`delete_transaction`/`update_category`
kabi tool'lar boshqa foydalanuvchining ID'si bilan chaqirilsa **har doim**
`not_found` qaytaradi (`403` emas) — bu ataylab shunday: xato tool
natijasi ichida (200 OK JSON-RPC javobida) keladi, AI uni o'qib
foydalanuvchiga tushuntiradi.

---

## 5. Sahifalash formati

Ro'yxat qaytaradigan tool'lar (`list_transactions`, `get_reports`,
`list_categories`, `get_debts_detail`, `list_closed_debts`, ...):

```json
{
  "items": [...],
  "summaries": {"count": 47, "income": 1250000, "expense": 830000},
  "meta": {"page": 1, "limit": 20, "hasMore": true}
}
```

`summaries` — **BUTUN natija** bo'yicha jami (faqat shu sahifa emas).

---

## 6. Xavfsizlik

- Har SQL so'rov `telegram_id`/`user_id` bilan filtrlangan (P0 auditi +
  yon IDOR tuzatishlari — `docs/` tarixidagi PR'larga qarang).
- Rate limit: bitta token uchun daqiqasiga 60 so'rov.
- Har chaqiruv `mcp_audit_log` jadvaliga yoziladi (summalar emas, faqat
  hash — maxfiylik uchun).
- Token hash (SHA-256) saqlanadi, ochiq qiymat hech qayerda turmaydi.

---

## 7. Cheklovlar (ochiq va halol)

- **Bitta valyuta** — faqat UZS. `transactions.currency` ustuni bor,
  lekin amalda hech qayerda ishlatilmaydi.
- **`set_language`/`set_timezone` YO'Q** — bot butunlay o'zbek tilida va
  Asia/Tashkent vaqt zonasida ishlaydi, buni "o'zgartiradigan" tool
  qasddan qo'shilmadi (haqiqiy i18n/tz infratuzilmasi yo'q holda bu
  aldamchi bo'lar edi).
- **Kategoriya bo'yicha alohida budjet YO'Q** — faqat yagona oylik
  budjet (`set_budget`).
- **Qarzlar bitta valyutada** — `debts` jadvalida currency ustuni yo'q.
