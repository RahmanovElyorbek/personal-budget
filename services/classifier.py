"""
services/classifier.py — Oson Byudjet uchun 3 qatlamli tranzaksiya klassifikatori.

VAZIFA: matndan (yozma YOKI ovozdan tanilgan) bir yoki bir nechta moliyaviy
amaliyotni ajratib olish, har biriga to'g'ri KATEGORIYA va YO'NALISH
(kirim/chiqim) berish.

Bu modulni QUYIDAGILAR chaqiradi:
  - budget_bot_webhook.py -> parse_voice_transactions() — OVOZDAN tanilgan
    matn HAM, foydalanuvchi TERIB yozgan matn HAM shu funksiyaga (demak shu
    modulga) keladi. Ikkalasi ham bitta yo'l.

Bu modul QUYIDAGILARGA umuman aloqasi yo'q va ularni o'zgartirmaydi:
  - transcribe_voice() — audio -> matn (Whisper). Chin ma'noda "ovozli
    kiritish tizimi" shu.
  - analyze_receipt_image() — chek rasmi tahlili (GPT-4o Vision).

Qatlamlar:
  1-qatlam — o'zbekcha kalit so'z lug'ati (regex, so'z boshi mos kelishi,
             so'z oxiri ochiq — qo'shimchalarga ruxsat).
  2-qatlam — boyitilgan AI prompt (GPT-4o-mini, temperature=0).
  3-qatlam — validatsiya: AI va lug'at natijalarini solishtiradi, zarur
             bo'lsa AI'ga qisqa majburiy qayta so'rov yuboradi.

Har bir qaytgan tranzaksiyada `source` maydoni bor: "ai" | "keyword" |
"ai+keyword" | "ai-retry" — keyinchalik lug'atni qaysi so'zlar bilan
boyitish kerakligini ko'rsatish uchun (transactions.source ustuniga
yoziladi — migratsiya alohida bosqichda).
"""

import json
import logging
import re
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"

# ===================== KATEGORIYALAR (o'zgarmas — mavjud DB yozuvlari va
# tugma indekslariga bog'liq, tartib va matnni o'zgartirish TAQIQLANADI) ====

EXPENSE_CATEGORIES = [
    "🍔 Oziq-ovqat", "🚌 Transport", "🏠 Uy-joy", "💊 Salomatlik",
    "🎮 Ko'ngil ochar", "👗 Kiyim-kechak", "📚 Ta'lim", "💡 Kommunal",
    "📱 Aloqa", "🎁 Sovg'alar", "🏋️ Sport", "✈️ Sayohat", "📦 Boshqa"
]
INCOME_CATEGORIES = [
    "💼 Maosh", "💻 Freelance", "📈 Investitsiya", "🎁 Sovg'a",
    "🏦 Bank foizi", "🛒 Sotish", "📦 Boshqa daromad"
]

DEFAULT_EXPENSE_CATEGORY = "📦 Boshqa"
DEFAULT_INCOME_CATEGORY = "📦 Boshqa daromad"

# ===================== 1-QATLAM: KALIT SO'Z LUG'ATI =====================
# Har kategoriya: desc (AI promptga chiqadigan ta'rif) + keywords (lug'at
# qatlami uchun, so'z boshidan mos kelishi tekshiriladi, oxiri ochiq).
# Jami lug'at 400 so'zdan oshadi (pastda tekshiriladi, test faylida ham).

EXPENSE_CATEGORY_DEFS = {
    "🍔 Oziq-ovqat": {
        "desc": "Ovqat, ichimlik, bozor-do'kondan xarid, oshxona/kafe/restoran xarajatlari",
        "keywords": [
            "go'sht", "mol go'shti", "qo'y go'shti", "tovuq go'shti", "baliq",
            "non", "lepyoshka", "sut", "qatiq", "tvorog", "pishloq", "sariyog'",
            "guruch", "un", "makaron", "yormalar", "yog'", "kungaboqar yog'i",
            "tuxum", "kartoshka", "piyoz", "sabzi", "karam", "bodring", "pomidor",
            "baqlajon", "qalampir", "ko'katlar", "petrushka", "ukrop", "olma",
            "uzum", "anor", "bexi", "tarvuz", "qovun", "shaftoli", "o'rik",
            "banan", "apelsin", "limon", "mandarin", "yong'oq", "mayiz",
            "konserva", "murabbo", "asal", "ziravor", "tuz", "shakar", "qand",
            "choy", "kofe", "kakao", "suv", "mineral suv", "sok", "gazli ichimlik",
            "tort", "keks", "pechene", "shokolad", "konfet", "muzqaymoq",
            "bozorlik", "bozorga bordim", "do'kon", "dokonga bordim", "market",
            "supermarket", "korzinka", "makro", "havas", "oshxona", "choyxona",
            "choyxonaga", "kafe", "restoran", "fastfud", "osh", "somsa",
            "shashlik", "lag'mon", "manti", "chuchvara", "norin", "belyash",
            "hasip", "kabob", "pitsa", "burger", "hotdog", "shaurma", "salat",
            "tushlik", "tushlikka", "nonushta", "kechki ovqat", "kechki ovqatga",
            "ovqat", "ovqatga", "taomxona", "yeguliq", "azuqa", "oziq-ovqat",
            "oziqovqat",
        ],
    },
    "🚌 Transport": {
        "desc": "Jamoat/shaxsiy transport, yoqilg'i, mashina xizmati",
        "keywords": [
            "yo'lkira", "yo'l kira", "taksi", "yandex", "yandeks", "avtobus",
            "marshrutka", "damas", "elektrobus", "metro", "poyezd", "poezd",
            "benzin", "metan", "propan",
            "zapravka", "moyka", "shina", "shinomontaj", "parkovka", "to'xtash joyi",
            "avtoservis", "avtoservisga", "mashina ta'miri", "mashina remonti",
            "yog' almashtirish", "mashina sug'urtasi", "texosmotr", "shtraf",
            "yo'l jarimasi", "poyezd bileti", "aeroport taksisi", "yoqilg'i",
            "yoqilgi", "benzin quydirdim", "gaz quydirdim",
        ],
    },
    "🏠 Uy-joy": {
        "desc": "Ijara, kommunal bo'lmagan uy xarajatlari, ta'mir, ro'zg'or buyumlari",
        "keywords": [
            "ijara", "arenda", "kvartira puli", "uy puli", "remont", "ta'mir",
            "sement", "g'isht", "bo'yoq", "shpaklyovka", "laminat", "parket",
            "kafel", "santexnika", "kran", "trubalar", "elektrik", "elektr ustasi",
            "usta chaqirdim", "mebel", "divan", "krovat", "shkaf", "stol-stul",
            "gilam", "parda", "muzlatgich", "kir yuvish mashinasi", "changyutgich",
            "idish-tovoq", "qozon", "tova", "poroshok", "kir yuvish poroshogi",
            "sovun", "yuvish vositasi", "ro'zg'or", "ro'zg'or buyumi", "santexnik",
            "uy jihozi", "uy anjomlari", "uy uchun",
        ],
    },
    "💊 Salomatlik": {
        "desc": "Dori-darmon, shifokor, tibbiy xizmat",
        "keywords": [
            "dori", "dorixona", "apteka", "tabletka", "sirop", "ukol", "vitamin",
            "shifokor", "doktor", "klinika", "gospital", "kasalxona", "analiz",
            "tahlil", "uzi", "rentgen", "tish", "stomatolog", "tish davolatdim",
            "ko'zoynak", "linza", "massaj", "davolanish", "operatsiya",
            "ambulatoriya", "poliklinika", "ko'rikdan o'tdim", "vrach",
            "tibbiyot", "tibbiy xizmat", "sog'liq",
        ],
    },
    "🎮 Ko'ngil ochar": {
        "desc": "Ko'ngilochar tadbir, dam olish, o'yin, kino",
        "keywords": [
            "kino", "kinoteatr", "park", "attraksion", "bilyard", "o'yin zali",
            "netflix", "kino obunasi", "sayr", "sayrga chiqdik",
            "piknik", "zoopark", "konsert", "teatr", "disko", "klub", "aquapark",
            "suv parki", "o'yin maydonchasi", "bouling", "karaoke", "sirk",
            "ko'ngilochar", "dam oldik",
        ],
    },
    "👗 Kiyim-kechak": {
        "desc": "Kiyim, poyabzal, go'zallik va parvarish xizmatlari",
        "keywords": [
            "ko'ylak", "shim", "jinsi", "futbolka", "kofta", "poyabzal",
            "krossovka", "tufli", "etik", "kurtka", "palto", "do'ppi", "ro'mol",
            "sumka", "hamyon", "atir", "parfyum", "kosmetika", "krem",
            "sartarosh", "sartaroshxona", "sartaroshxonaga", "soch oldirdim",
            "manikyur", "pedikyur", "pardoz", "go'zallik saloni", "salon",
            "kiyim", "kiyim-kechak", "oyoq kiyim", "aksessuar", "qo'l soati",
        ],
    },
    "📚 Ta'lim": {
        "desc": "Ta'lim, o'quv kurslari, maktab/bog'cha to'lovlari",
        "keywords": [
            "bog'cha", "sadik", "bolalar bog'chasi", "maktab", "litsey",
            "kollej", "universitet", "institut", "kontrakt", "kontrakt puli",
            "kurs", "kursga", "repetitor", "repetitorlik", "kitob", "daftar",
            "ruchka", "qalam", "maktab buyumlari", "ielts", "cefr", "toefl",
            "o'quv qo'llanma", "darslik", "ta'lim markazi", "imtihon puli",
            "o'qish puli", "akademik litsey", "kurslar", "ingliz tili kursi",
            "dasturlash kursi",
        ],
    },
    "💡 Kommunal": {
        "desc": "Kommunal to'lovlar (svet, gaz, suv, issiqlik, mahalla)",
        "keywords": [
            "svet", "svet puli", "elektr", "elektr energiyasi", "hududgaz",
            "gaz puli", "tabiiy gaz", "suv puli", "vodokanal", "issiqlik",
            "isitish", "markaziy isitish", "chiqindi", "chiqindi puli",
            "mahalla to'lovi", "kommunal", "kommunal to'lov", "kommunal xizmat",
        ],
    },
    "📱 Aloqa": {
        "desc": "Telefon, internet va aloqa xizmatlari",
        "keywords": [
            "telefon puli", "balans", "balans tashladim", "tarif", "internet",
            "wifi", "wi-fi", "ucell", "beeline", "uzmobile", "mobiuz", "humans",
            "perfectum", "telefon to'ldirdim", "aloqa xizmati", "aloqa puli",
            "internet puli", "raqam to'ldirdim",
        ],
    },
    "🎁 Sovg'alar": {
        "desc": "Sovg'a, to'y, ehson va marosim xarajatlari (berilgan pul)",
        "keywords": [
            "sovg'a", "to'y", "to'yga", "nikoh",
            "fotiha", "ma'raka", "ehson", "xudoyi", "sadaqa", "xayriya",
            "mehmon", "mehmonga", "ziyofat", "guldasta", "gul sotib oldim",
            "kelin salom", "sunnat to'yi", "muchal to'yi", "besh yoshga",
            "beshik to'yi", "yordam puli",
        ],
    },
    "🏋️ Sport": {
        "desc": "Sport zali, mashg'ulot, sport anjomlari",
        "keywords": [
            "sport zal", "sportzal", "trenajyor", "fitnes", "fitness",
            "abonement", "trener", "murabbiy", "basseyn", "protein",
            "sport anjomlari", "sport kiyimi", "yoga", "boks zali",
        ],
    },
    "✈️ Sayohat": {
        "desc": "Sayohat, turizm, aviabilet, mehmonxona",
        "keywords": [
            "sayohat", "sayohatga", "samolyot", "aviabilet", "otel",
            "mehmonxona", "gostinitsa", "viza", "tur", "turpaket", "sanatoriy",
            "umra", "hajga", "ziyorat", "kruiz", "sayyohlik",
        ],
    },
    "📦 Boshqa": {
        "desc": "Yuqoridagilarning hech biriga tushmaydigan, mutlaqo boshqa xarajat "
                "(masalan: notarius, sud boji, davlat boji)",
        "keywords": [],
    },
}

INCOME_CATEGORY_DEFS = {
    "💼 Maosh": {
        "desc": "Ish haqi, oylik maosh, avans, mukofot",
        "keywords": [
            "maosh", "oylik", "oylik maosh", "ish haqi", "zarplata", "avans",
            "premiya", "mukofot pul", "ustama", "13-maosh", "bonus",
        ],
    },
    "💻 Freelance": {
        "desc": "Frilans, masofaviy/loyihaviy ish puli",
        "keywords": [
            "frilans", "freelance", "loyiha puli", "frilans buyurtmasi",
            "dasturlash puli", "dizayn puli", "tarjima puli", "onlayn ish puli",
            "uzoqdan ish puli", "saytdan pul",
        ],
    },
    "📈 Investitsiya": {
        "desc": "Investitsiya daromadi, aksiya/kripto qaytimi",
        "keywords": [
            "investitsiya", "aksiya", "ulush daromadi", "dividend",
            "investitsiya foizi", "kripto", "kriptovalyuta",
            "investitsiya qaytimi",
        ],
    },
    "🎁 Sovg'a": {
        "desc": "Sovg'a sifatida olingan pul",
        "keywords": [
            "sovg'a puli", "hadya", "sovg'aga berishdi", "tug'ilgan kun puli",
            "sovg'a berishdi", "sovg'a qilishdi",
        ],
    },
    "🏦 Bank foizi": {
        "desc": "Bank/omonat/depozit foizi",
        "keywords": [
            "bank foizi", "depozit foizi", "omonat foizi", "karta foizi",
            "hisob foizi",
        ],
    },
    "🛒 Sotish": {
        "desc": "Savdo-sotiq, biznes tushumi, mahsulot/xizmat sotish",
        "keywords": [
            "sotdim", "sotuv", "savdo", "mijoz to'ladi", "mijoz to'lov qildi",
            "tushum", "kassa", "mijoz buyurtmasi", "mahsulot sotdim",
            "xizmat puli oldim", "biznes tushumi", "do'kon savdosi",
        ],
    },
    "📦 Boshqa daromad": {
        "desc": "Yuqoridagilarning hech biriga tushmaydigan boshqa daromad",
        "keywords": [],
    },
}

# ===================== APOSTROF NORMALIZATSIYASI =====================
# O'zbek tilida turli apostrof belgilari uchraydi: ' ` ' ' ʻ ʼ ´ ʹ
# Hammasi bitta shaklga (oddiy ') keltiriladi — bu shu bilan lug'atda
# ishlatilgan shaklga ham mos keladi.
_APOSTROPHE_VARIANTS = "'`‘’ʻʼ´ʹ"
_APOSTROPHE_RE = re.compile(f"[{re.escape(_APOSTROPHE_VARIANTS)}]")


def normalize_text(text: str) -> str:
    """Apostroflarni bitta shaklga keltiradi va kichik harfga o'tkazadi."""
    if not text:
        return ""
    return _APOSTROPHE_RE.sub("'", text.lower())


def _keyword_regex(keyword: str) -> re.Pattern:
    """Kalit so'z uchun regex quradi: so'z BOSHI aniq chegaralangan
    (harf/raqam/apostrofdan keyin kelmasin), so'z OXIRI ochiq (o'zbekcha
    qo'shimchalar — 'choyxona' -> 'choyxonaga' — davom etishi mumkin).
    Ko'p so'zli iboralar ('sotib oldim') ham ishlaydi."""
    escaped = re.escape(normalize_text(keyword))
    return re.compile(rf"(?<![a-zA-Z'0-9]){escaped}")


_KEYWORD_REGEX_CACHE: dict = {}


def _get_keyword_regex(keyword: str) -> re.Pattern:
    rx = _KEYWORD_REGEX_CACHE.get(keyword)
    if rx is None:
        rx = _keyword_regex(keyword)
        _KEYWORD_REGEX_CACHE[keyword] = rx
    return rx


def _keyword_score(keyword: str) -> int:
    """Uzunroq/ko'p so'zli ibora kuchliroq dalil hisoblanadi."""
    return len(keyword.split()) * 10 + len(keyword)


def classify_by_keywords(text: str, txn_type: str) -> "tuple[str | None, int]":
    """1-qatlam: lug'at asosida kategoriya topadi.
    txn_type: 'expense' yoki 'income'.
    Qaytaradi: (eng mos kategoriya yoki None, umumiy ball)."""
    norm = normalize_text(text)
    defs = EXPENSE_CATEGORY_DEFS if txn_type == "expense" else INCOME_CATEGORY_DEFS

    scores: dict = {}
    for category, info in defs.items():
        total = 0
        for kw in info["keywords"]:
            rx = _get_keyword_regex(kw)
            matches = rx.findall(norm)
            if matches:
                total += _keyword_score(kw) * len(matches)
        if total > 0:
            scores[category] = total

    if not scores:
        return None, 0

    best_category = max(scores, key=scores.get)
    return best_category, scores[best_category]


# ===================== YO'NALISH (KIRIM/CHIQIM) ANIQLASH =====================
# Bular TO'LIQ so'z shaklida (fe'l allaqachon tuslangan) — shuning uchun
# ikkala tomondan HAM chegaralanadi, aks holda "to'ladim" so'zi "to'ladi"
# kalit so'ziga soxta mos kelib qoladi.

EXPENSE_SIGNAL_VERBS = [
    "oldim", "sotib oldim", "berdim", "to'ladim", "ketdi", "bordim",
    "sarfladim", "xarjladim", "harjladim", "ishlatdim", "to'lov qildim",
]
INCOME_SIGNAL_VERBS = [
    "tushdi", "keldi", "sotdim", "qaytardi", "berishdi", "to'ladi",
    "kirdi", "ishlab topdim", "hisobimga tushdi", "kartaga tushdi",
    "pul yubordi", "o'tkazma qildi",
]


def _whole_word_present(norm_text: str, phrase: str) -> bool:
    """Ibora matnda TO'LIQ so'z/ibora sifatida bormi (ikkala chetdan ham
    chegaralangan — qo'shimcha bilan davom etsa mos kelmaydi)."""
    escaped = re.escape(normalize_text(phrase))
    rx = re.compile(rf"(?<![a-zA-Z'0-9]){escaped}(?![a-zA-Z'0-9])")
    return rx.search(norm_text) is not None


def detect_direction(text: str) -> str:
    """Fe'l asosida YO'NALISHNI AI'dan MUSTAQIL aniqlaydi (ikkinchi himoya
    qatlami). Bu funksiya kategoriyani yoki mahsulot nomini emas, FAQAT
    fe'lni tekshiradi — shuning uchun 'olma oldim' kabi holatlarda mahsulot
    nomi ('olma') signalga aylanib ketmaydi.

    Standart va shubhali holatda har doim 'chiqim' qaytaradi."""
    norm = normalize_text(text)
    has_income = any(_whole_word_present(norm, v) for v in INCOME_SIGNAL_VERBS)
    has_expense = any(_whole_word_present(norm, v) for v in EXPENSE_SIGNAL_VERBS)
    if has_income and not has_expense:
        return "kirim"
    return "chiqim"


def validate_direction(ai_type: str, segment_text: str) -> "tuple[str, bool]":
    """AI 'income' desa-yu, matnda hech qanday kirim signali bo'lmasa,
    xavfsiz tomonga (chiqim/expense) majburan o'zgartiradi.
    Qaytaradi: (yakuniy_type, override_bo'ldimi)."""
    if ai_type != "income":
        return ai_type, False
    if detect_direction(segment_text) == "kirim":
        return "income", False
    return "expense", True


# ===================== MATNNI QISMLARGA AJRATISH =====================
# AI bir xabardagi bir nechta amaliyotni odatda vergul/'va'/'hamda'/'keyin'/
# "so'ngra" bilan ajratadi. Har bir tranzaksiyani o'zining ORIGINAL matn
# bo'lagiga (note emas — note AI tomonidan qayta ifodalangan bo'lishi
# mumkin) bog'lash uchun xabarni ham xuddi shunday bo'laklarga bo'lamiz.

_SEGMENT_SPLIT_RE = re.compile(r",|;|\bva\b|\bhamda\b|\bkeyin\b|\bso'ngra\b", re.IGNORECASE)


def split_segments(text: str) -> "list[str]":
    norm = normalize_text(text)
    parts = [p.strip() for p in _SEGMENT_SPLIT_RE.split(norm) if p.strip()]
    return parts or [norm]


def _segment_for(segments: "list[str]", idx: int, fallback: str) -> str:
    if idx < len(segments):
        return segments[idx]
    return fallback


# ===================== 2-QATLAM: AI PROMPT =====================

UZBEK_WEEKDAYS = [
    "dushanba", "seshanba", "chorshanba", "payshanba",
    "juma", "shanba", "yakshanba",
]


def _category_lines(defs: dict) -> str:
    lines = []
    for cat, info in defs.items():
        if not info["keywords"]:
            continue
        examples = ", ".join(info["keywords"][:14])
        lines.append(f"  • {cat} — {info['desc']}. Misol so'zlar: {examples}")
    return "\n".join(lines)


def build_system_prompt(today: date) -> str:
    expense_lines = _category_lines(EXPENSE_CATEGORY_DEFS)
    income_lines = _category_lines(INCOME_CATEGORY_DEFS)
    expense_cats = ", ".join(EXPENSE_CATEGORIES)
    income_cats = ", ".join(INCOME_CATEGORIES)
    today_iso = today.isoformat()
    today_weekday = UZBEK_WEEKDAYS[today.weekday()]
    yesterday_iso = (today - timedelta(days=1)).isoformat()

    return (
        "Sen o'zbek tilidagi moliyaviy xabarlarni (yozma YOKI ovozdan tanilgan "
        "matn) tahlil qiluvchi yordamchisan.\n"
        "Foydalanuvchi BITTA xabarda BIR, IKKI YOKI UCHTA moliyaviy amaliyotni "
        "ketma-ket aytishi mumkin (odatda vergul, 'va', 'hamda', 'keyin', "
        "'so'ngra' bilan ajratiladi). Har bir alohida amaliyotni topib, alohida "
        "obyekt sifatida chiqar — hech birini tushirib qoldirma.\n\n"
        "MUHIM: Har doim shu formatda JSON qaytar:\n"
        '{"transactions": [ {...}, {...} ]}\n\n'
        "Har bir amaliyot uchun quyidagi maydonlar:\n\n"
        "1. type: 'income' | 'expense' | 'debt_gave' | 'debt_took' | 'debt_repay'\n"
        "   - 'sarfladim/(mahsulot uchun) berdim/to'ladim/xarjladim/oldim (sotib)' = expense\n"
        "   - 'maosh/tushdi/kirdi/daromad/ishlab topdim/sotdim' = income\n"
        "   - YANGI qarz: 'qarz berdim/qarzga berdim/qarz qildim (biror kishiga)' = debt_gave\n"
        "   - YANGI qarz: 'qarz oldim/qarzga oldim/nasiya oldim' = debt_took\n"
        "   - MAVJUD qarzni YOPISH (yangi qarz EMAS, eskisini yopish): "
        "'qarz to'lovi', 'eski qarz', 'qarzimni qaytardim', 'qarzini qaytardi', "
        "'nasiyani uzdim', 'qarzdan tushdi', 'hisoblashdik' = debt_repay\n"
        "   ENG MUHIM FARQ: 'qarz oldim' (YANGI qarz olyapman) bilan "
        "'qarzimni qaytardim' (ESKI qarzni yopyapman, o'zim to'layapman) "
        "ikkisi BUTUNLAY BOSHQA — birinchisi debt_took, ikkinchisi debt_repay. "
        "'qaytardim/qaytardi/to'ladim/to'lovi/uzdim' so'zlari MAVJUD qarz "
        "haqida ekanini bildiradi → debt_repay.\n"
        "   MUHIM: 'qarz' yoki 'nasiya' so'zi ANIQ aytilmagan bo'lsa, buni debt "
        "deb hisoblama — oddiy 'berdim' (masalan qizimga/o'g'limga pul berish) "
        "odatda EXPENSE bo'ladi, debt EMAS. Istisno: 'hisoblashdik' so'zi ham "
        "debt_repay signali (odatda ikki kishi orasidagi qarz-hisobni yopish).\n\n"
        "2. YO'NALISH (income/expense uchun) — BU ENG MUHIM QOIDA:\n"
        "   - Pul KETGAN bo'lsa → \"expense\".\n"
        "     Fe'llar: oldim, sotib oldim, berdim, to'ladim, ketdi, bordim, sarfladim\n"
        "   - Pul KELGAN bo'lsa → \"income\".\n"
        "     Fe'llar: tushdi, keldi, sotdim, qaytardi, berishdi\n"
        "   MUHIM: \"olma oldim\", \"kitob oldim\", \"dori oldim\" — bularda "
        "\"oldim\" SOTIB OLDIM ma'nosida, ya'ni EXPENSE. Mahsulot NOMINI "
        "(masalan \"olma\" mevasini) kirim signali deb HECH QACHON o'ylama.\n"
        "   Shubha bo'lsa → har doim \"expense\".\n\n"
        "3. amount: butun son (so'mda). O'zbekcha sonlarni QISMLARDAN QO'SHIB hisobla:\n"
        "   - 'ming'=1 000, 'o'n ming'=10 000, 'yigirma ming'=20 000, 'ellik ming'=50 000\n"
        "   - 'yuz ming'=100 000, 'ikki yuz ming'=200 000, 'uch yuz ming'=300 000\n"
        "   - 'yuz ellik ming'=150 000 ('yuz'+'ellik ming' qo'shiladi)\n"
        "   - 'million'=1 000 000, 'bir yarim million'=1 500 000\n"
        "   QOIDA: har bir son so'zini alohida qiymatga aylantirib, ULARNI QO'SH "
        "(masalan 'ikki yuz o'n besh ming' = 200000+10000+5000 = 215000).\n"
        "   Agar audio matni buzilgan yoki tanish bo'lmagan tovushga o'xshasa "
        "(masalan 'üçüz', 'dörüz', 'beşüz' kabi turkcha aralashgan so'zlar "
        "chiqsa), buni mos o'zbekcha songa mosla: üçüz≈uch yuz, dörüz≈to'rt "
        "yuz, beşüz≈besh yuz — FAQAT boshqa aniqroq talqin topilmaganda ishlat.\n\n"
        "4. category: faqat income/expense uchun (debt uchun null). "
        "Quyidagi ro'yxatdan ANIQ BIRINI tanla — TAVSIF va MISOL SO'ZLARGA "
        "qarab eng mosini top:\n\n"
        f"XARAJAT kategoriyalari:\n{expense_lines}\n\n"
        f"DAROMAD kategoriyalari:\n{income_lines}\n\n"
        f"   Faqat shu ro'yxatdan biri qaytishi SHART: {expense_cats} / {income_cats}\n"
        "   QAT'IY QOIDA: \"📦 Boshqa\" / \"📦 Boshqa daromad\" ni tanlash "
        "TAQIQLANADI, agar matnda yuqoridagi kategoriyalardan biriga mos "
        "so'z-belgi bo'lsa. \"Boshqa\" FAQAT mutlaqo hech qaysi kategoriyaga "
        "tushmaydigan holat uchun (masalan: notarius, sud boji, davlat boji).\n"
        "   Aniq ko'rsatmalar:\n"
        "   - Choyxona / kafe / restoran / somsa / osh → oziq-ovqat\n"
        "   - Bog'cha / maktab / kurs to'lovi → ta'lim\n"
        "   - Yo'lkira / taksi / benzin → transport\n"
        "   - To'y / sovg'a / ehson / ma'raka → sovg'alar\n"
        "   - Sovun / poroshok / idish / ro'zg'or → uy-joy\n\n"
        "5. note: qisqa izoh (3-8 so'z), ASL matndagi so'zlarni ishlat (masalan "
        "\"bollar bilan choyxonaga\", \"bog'chaga to'lov\") — o'zingdan sarlavha "
        "o'ylab topma.\n\n"
        "6. person: faqat debt_gave/debt_took/debt_repay uchun — kim (masalan "
        "'Sardor'). Matnda ism aytilmasa (masalan \"eski qarz to'lovi\") — null "
        "qoldir, o'zingdan ism o'ylab topma.\n\n"
        f"7. date: amaliyot SODIR BO'LGAN sana, 'YYYY-MM-DD' formatida.\n"
        f"   Bugungi sana: {today_iso} ({today_weekday}).\n"
        "   Matnda vaqt signali bo'lsa hisobla:\n"
        "   - 'bugun' yoki hech qanday vaqt signali yo'q -> bugungi sana\n"
        "   - 'kecha' -> bugungi sanadan 1 kun ayir\n"
        "   - 'ertaga' -> bugungi sanaga 1 kun qo'sh\n"
        "   - 'o'tgan juma', 'o'tgan payshanba' kabi -> shu haftaning ko'rsatilgan "
        "kuni (agar bugun ham shu kun bo'lsa, o'tgan haftaga hisobla)\n"
        "   - '1-sentabrda', '15-avgustda' kabi aniq kun-oy -> joriy yilning shu "
        "sanasi (agar bu sana bugundan KEYIN chiqib qolsa, o'tgan yilga hisobla)\n"
        "   Signal topilmasa har doim bugungi sana — hech qachon bo'sh qoldirma.\n\n"
        "MISOL 1 kirish: 'bozordan olti yuz ming bozorlik qildim, mashinaga ikki "
        "yuz ming yoqilg'i quydirdim, Sardorga uch yuz ming qarz berdim'\n"
        "MISOL 1 chiqish:\n"
        '{"transactions": ['
        f'{{"type":"expense","amount":600000,"category":"🍔 Oziq-ovqat","note":"Bozorlik","person":null,"date":"{today_iso}"}},'
        f'{{"type":"expense","amount":200000,"category":"🚌 Transport","note":"Yoqilg\'i","person":null,"date":"{today_iso}"}},'
        f'{{"type":"debt_gave","amount":300000,"category":null,"note":"Qarz berildi","person":"Sardor","date":"{today_iso}"}}'
        ']}\n\n'
        "MISOL 2 kirish: 'qizimga o'n ming so'm berdim, mashinaga ellik ming "
        "so'mlik benzin quydim, oyligimni oldim uch million so'm'\n"
        "MISOL 2 chiqish (3 ta amaliyot, 'qarz' so'zi yo'q — hammasi expense/income):\n"
        '{"transactions": ['
        f'{{"type":"expense","amount":10000,"category":"📦 Boshqa","note":"Qizimga berildi","person":null,"date":"{today_iso}"}},'
        f'{{"type":"expense","amount":50000,"category":"🚌 Transport","note":"Benzin","person":null,"date":"{today_iso}"}},'
        f'{{"type":"income","amount":3000000,"category":"💼 Maosh","note":"Oylik maosh","person":null,"date":"{today_iso}"}}'
        ']}\n\n'
        "MISOL 3 kirish: '100000 so'm bollar bilan choyxonaga bordik'\n"
        "MISOL 3 chiqish:\n"
        '{"transactions": [{"type":"expense","amount":100000,"category":"🍔 Oziq-ovqat",'
        f'"note":"Bollar bilan choyxonaga","person":null,"date":"{today_iso}"}}]}}\n\n'
        "MISOL 4 kirish: '40000 so'm bog'chaga to'lov qilindi'\n"
        "MISOL 4 chiqish:\n"
        '{"transactions": [{"type":"expense","amount":40000,"category":"📚 Ta\'lim",'
        f'"note":"Bog\'chaga to\'lov","person":null,"date":"{today_iso}"}}]}}\n\n'
        "MISOL 5 kirish: '20000 so'm olmaga, 30000 so'm Mohinurga yo'lkira, "
        "40000 so'm bog'chaga to'lov qilindi'\n"
        "MISOL 5 chiqish (3 ta amaliyot, hammasi EXPENSE — 'olmaga' meva nomi, "
        "kirim EMAS):\n"
        '{"transactions": ['
        f'{{"type":"expense","amount":20000,"category":"🍔 Oziq-ovqat","note":"Olmaga","person":null,"date":"{today_iso}"}},'
        f'{{"type":"expense","amount":30000,"category":"🚌 Transport","note":"Mohinurga yo\'lkira","person":null,"date":"{today_iso}"}},'
        f'{{"type":"expense","amount":40000,"category":"📚 Ta\'lim","note":"Bog\'chaga to\'lov","person":null,"date":"{today_iso}"}}'
        ']}\n\n'
        "MISOL 6 kirish: '2050000 so'm oylik maosh tushdi'\n"
        "MISOL 6 chiqish:\n"
        '{"transactions": [{"type":"income","amount":2050000,"category":"💼 Maosh",'
        f'"note":"Oylik maosh tushdi","person":null,"date":"{today_iso}"}}]}}\n\n'
        "MISOL 7 kirish: 'mijoz 500000 to'ladi'\n"
        "MISOL 7 chiqish:\n"
        '{"transactions": [{"type":"income","amount":500000,"category":"🛒 Sotish",'
        f'"note":"Mijoz to\'lovi","person":null,"date":"{today_iso}"}}]}}\n\n'
        "MISOL 8 kirish: '600000 eski qarz to'lovi'\n"
        "MISOL 8 chiqish (bu YANGI xarajat EMAS, MAVJUD qarzni yopish — "
        "ism aytilmagan, person null qoladi):\n"
        '{"transactions": [{"type":"debt_repay","amount":600000,"category":null,'
        f'"note":"Eski qarz to\'lovi","person":null,"date":"{today_iso}"}}]}}\n\n'
        "MISOL 9 kirish: 'Sardorga bergan qarzimni qaytardi'\n"
        "MISOL 9 chiqish (Sardor MENGA qarzini qaytardi — MAVJUD qarzni yopish):\n"
        '{"transactions": [{"type":"debt_repay","amount":0,"category":null,'
        f'"note":"Sardor qarzini qaytardi","person":"Sardor","date":"{today_iso}"}}]}}\n\n'
        "MISOL 10 kirish: 'Kecha 100000 so'm bollar bilan choyxonaga bordik, "
        "27000 so'mga uyga bozorlik qildim'\n"
        f"MISOL 10 chiqish (2 ta amaliyot, 'kecha' -> {yesterday_iso}, ikkalasiga "
        "ham tegishli — butun gapga bitta vaqt signali qo'llanadi):\n"
        '{"transactions": ['
        f'{{"type":"expense","amount":100000,"category":"🍔 Oziq-ovqat","note":"Bollar bilan choyxonaga","person":null,"date":"{yesterday_iso}"}},'
        f'{{"type":"expense","amount":27000,"category":"🍔 Oziq-ovqat","note":"Uyga bozorlik","person":null,"date":"{yesterday_iso}"}}'
        ']}\n\n'
        "Tushunarsiz bo'lsa: {\"transactions\": []}\n"
        "FAQAT JSON qaytar, boshqa hech narsa yozma!"
    )


_FORCE_CATEGORY_PROMPT_TMPL = (
    "Sen 'boshqa' ni tanlading, bu qabul qilinmaydi. Quyidagi ro'yxatdan "
    "matnga ENG YAQININI majburan tanla. Faqat kategoriya matnini (emoji "
    "bilan birga) qaytar, boshqa hech narsa yozma:\n{options}\n\nMatn: {text}"
)


# ===================== OPENAI CHAQIRUVLARI =====================

async def _call_openai_json(system_prompt: str, user_text: str, api_key: str,
                             temperature: float = 0.0) -> "dict | None":
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"},
                },
            )
            if response.status_code != 200:
                logger.warning(f"GPT klassifikator xatosi: {response.text}")
                return None
            content = response.json()["choices"][0]["message"]["content"]
            logger.info(f"🤖 GPT klassifikator javobi: {content}")
            return json.loads(content)
    except Exception as e:
        logger.warning(f"GPT klassifikator so'rovida xato: {e}")
        return None


async def _ai_force_category(segment_text: str, txn_type: str, api_key: str) -> "str | None":
    """3-qatlam majburiy qayta so'rov: AI ikkinchi marta, faqat 'boshqa'dan
    tashqari kategoriyalardan birini tanlashga majbur qilinadi."""
    cats = [c for c in (EXPENSE_CATEGORIES if txn_type == "expense" else INCOME_CATEGORIES)
            if c not in (DEFAULT_EXPENSE_CATEGORY, DEFAULT_INCOME_CATEGORY)]
    prompt = _FORCE_CATEGORY_PROMPT_TMPL.format(options="\n".join(cats), text=segment_text)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "system", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 20,
                },
            )
            if response.status_code != 200:
                logger.warning(f"GPT majburiy qayta so'rov xatosi: {response.text}")
                return None
            content = response.json()["choices"][0]["message"]["content"].strip()
            for cat in cats:
                if cat in content:
                    return cat
            return None
    except Exception as e:
        logger.warning(f"GPT majburiy qayta so'rov istisnosi: {e}")
        return None


# ===================== 3-QATLAM: VALIDATSIYA =====================

async def _resolve_category(ai_category: "str | None", segment_text: str,
                             txn_type: str, api_key: str) -> "tuple[str, str]":
    """AI va lug'at natijalarini solishtirib yakuniy kategoriya va source
    ('ai' | 'keyword' | 'ai+keyword' | 'ai-retry')ni qaytaradi."""
    valid_cats = EXPENSE_CATEGORIES if txn_type == "expense" else INCOME_CATEGORIES
    default_cat = DEFAULT_EXPENSE_CATEGORY if txn_type == "expense" else DEFAULT_INCOME_CATEGORY

    kw_category, kw_score = classify_by_keywords(segment_text, txn_type)

    # 1) AI qaytargan kategoriya ro'yxatda yo'q bo'lsa -> lug'at natijasini ol
    if ai_category not in valid_cats:
        if kw_category:
            logger.info(
                f"Kategoriya tuzatildi (AI natijasi yaroqsiz '{ai_category}' -> "
                f"lug'at '{kw_category}'): {segment_text!r}")
            return kw_category, "keyword"
        return default_cat, "ai"

    is_ai_default = ai_category in (DEFAULT_EXPENSE_CATEGORY, DEFAULT_INCOME_CATEGORY)

    if is_ai_default:
        # 2) AI 'boshqa' desa, lekin lug'at aniq kategoriya topsa -> lug'at g'olib
        if kw_category:
            logger.info(
                f"Kategoriya tuzatildi (AI 'Boshqa' dedi, lug'at '{kw_category}' "
                f"topdi, ball={kw_score}): {segment_text!r}")
            return kw_category, "keyword"
        # 3) Lug'at ham topolmasa va kategoriya 'boshqa' bo'lsa -> majburiy qayta so'rov
        retry_cat = await _ai_force_category(segment_text, txn_type, api_key)
        if retry_cat and retry_cat in valid_cats:
            logger.info(
                f"Kategoriya majburiy qayta so'rov bilan aniqlandi: "
                f"'{retry_cat}': {segment_text!r}")
            return retry_cat, "ai-retry"
        return default_cat, "ai"

    # 4) Lug'at ballari juda yuqori va AI boshqa narsa desa -> lug'at g'olib
    if kw_category and kw_category != ai_category and kw_score >= 30:
        logger.info(
            f"Kategoriya tuzatildi (lug'at balli yuqori: {kw_score}, AI "
            f"'{ai_category}' dedi, lug'at '{kw_category}' g'olib): {segment_text!r}")
        return kw_category, "keyword"

    if kw_category == ai_category:
        return ai_category, "ai+keyword"
    return ai_category, "ai"


async def classify_transactions(text: str, api_key: str, today: "date | None" = None) -> "list[dict]":
    """Asosiy kirish nuqtasi: matndan (yozma yoki ovozdan tanilgan) bir yoki
    bir nechta tranzaksiyani ajratadi, kategoriya, yo'nalish va sanani 3
    qatlamli tekshiruvdan o'tkazadi.

    today: chaqiruvchi tomonidan mahalliy (Asia/Tashkent) bugungi sana
    sifatida beriladi — 'kecha'/'ertaga' kabi nisbiy sanalarni hisoblash
    shundan boshlanadi. Berilmasa server sanasi ishlatiladi (zaxira holat).

    Qaytadigan har bir dict: type, amount, category, note, person, source, date."""
    if today is None:
        today = date.today()

    if not text or not text.strip():
        return []

    if not api_key:
        logger.warning("OPENAI_API_KEY yo'q — klassifikator ishlay olmaydi.")
        return []

    system_prompt = build_system_prompt(today)
    parsed = await _call_openai_json(system_prompt, text, api_key, temperature=0.0)
    if not parsed:
        return []

    raw_list = parsed.get("transactions", [])
    segments = split_segments(text)

    results = []
    for i, item in enumerate(raw_list):
        ttype = item.get("type", "expense")
        if ttype not in ("income", "expense", "debt_gave", "debt_took", "debt_repay"):
            ttype = "expense"

        try:
            amount = float(item.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0
        # debt_repay uchun summa MAJBURIY EMAS — haqiqiy summa mavjud qarz
        # yozuvidan olinadi (matnda "eski qarzimni qaytardim" kabi summasiz
        # gap bo'lishi mumkin). Boshqa turlarda summasiz yozuv tashlab
        # yuboriladi.
        if amount <= 0 and ttype != "debt_repay":
            continue

        note = (item.get("note") or "")[:200]
        segment_text = _segment_for(segments, i, note or text)

        if ttype in ("income", "expense"):
            # 2-qatlam himoyasi: yo'nalishni fe'l asosida mustaqil tekshirish
            ttype, direction_overridden = validate_direction(ttype, segment_text)
            if direction_overridden:
                logger.info(
                    f"Yo'nalish tuzatildi (AI 'income' dedi, kirim signali "
                    f"topilmadi -> 'expense'): {segment_text!r}")

            category, source = await _resolve_category(
                item.get("category"), segment_text, ttype, api_key)
        else:
            category = None
            source = "ai"

        raw_date = item.get("date")
        tx_date = today
        if isinstance(raw_date, str) and raw_date.strip():
            try:
                tx_date = date.fromisoformat(raw_date.strip())
            except ValueError:
                logger.warning(f"Sana tanib bo'lmadi ('{raw_date}'), bugungi sana ishlatiladi: {segment_text!r}")
                tx_date = today
        # "ertaga" (today+1) qonuniy signal — shuni ruxsat beramiz, lekin
        # undan uzoqroq kelajak sanalari odatda AI xatosi, bugungi sanaga tushiramiz
        if tx_date > today + timedelta(days=1):
            tx_date = today

        results.append({
            "type": ttype,
            "amount": amount,
            "category": category,
            "note": note,
            "person": item.get("person"),
            "source": source,
            "date": tx_date,
        })

    return results
