"""
tests/test_classifier.py — services/classifier.py uchun AI'siz (tarmoqsiz)
testlar. Faqat 1-qatlam (kalit so'z lug'ati) va yo'nalish aniqlagichni
tekshiradi — OpenAI API chaqirilmaydi, shuning uchun bu testlar internetsiz
va bepul ishlaydi.

Ishga tushirish:
    python -m pytest tests/test_classifier.py -v
yoki pytest o'rnatilmagan bo'lsa:
    python tests/test_classifier.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.classifier import (
    classify_by_keywords,
    detect_direction,
    normalize_text,
    split_segments,
    EXPENSE_CATEGORY_DEFS,
    INCOME_CATEGORY_DEFS,
)

# ===================== 1) VAZIFA 1 — KATEGORIYA (lug'at qatlami) =====================

CATEGORY_CASES = [
    # (matn, txn_type, kutilgan_kategoriya)
    ("100000 so'm bollar bilan choyxonaga bordik", "expense", "🍔 Oziq-ovqat"),
    ("27000 so'mga uyga bozorlik qildim", "expense", "🍔 Oziq-ovqat"),
    ("40000 so'm bog'chaga to'lov qilindi", "expense", "📚 Ta'lim"),
    ("bozordan olti yuz ming bozorlik qildim", "expense", "🍔 Oziq-ovqat"),
    ("mashinaga ikki yuz ming yoqilg'i quydirdim", "expense", "🚌 Transport"),
    ("taksiga yigirma ming berdim", "expense", "🚌 Transport"),
    ("svet puliga to'ladim", "expense", "💡 Kommunal"),
    ("hududgazga to'lov qildim", "expense", "💡 Kommunal"),
    ("telefon balans tashladim", "expense", "📱 Aloqa"),
    ("internet puli to'ladim", "expense", "📱 Aloqa"),
    ("sport zaliga abonement oldim", "expense", "🏋️ Sport"),
    ("dorixonadan dori oldim", "expense", "💊 Salomatlik"),
    ("stomatologga tish davolatdim", "expense", "💊 Salomatlik"),
    ("kinoteatrga bordik", "expense", "🎮 Ko'ngil ochar"),
    ("ko'ylak va shim sotib oldim", "expense", "👗 Kiyim-kechak"),
    ("repetitorlik uchun to'ladim", "expense", "📚 Ta'lim"),
    ("to'yga sovg'a berdik", "expense", "🎁 Sovg'alar"),
    ("mehmonxonaga to'lov qildik", "expense", "✈️ Sayohat"),
    ("ijara puli to'ladim", "expense", "🏠 Uy-joy"),
    ("poroshok va sovun oldim", "expense", "🏠 Uy-joy"),
    ("oylik maosh tushdi", "income", "💼 Maosh"),
    ("frilans loyiha puli keldi", "income", "💻 Freelance"),
    ("do'kon savdosidan tushum", "income", "🛒 Sotish"),
]


def test_category_keyword_layer():
    ok, fail = 0, []
    for text, ttype, expected in CATEGORY_CASES:
        cat, _score = classify_by_keywords(text, ttype)
        if cat == expected:
            ok += 1
        else:
            fail.append((text, ttype, expected, cat))
    assert not fail, f"Kategoriya lug'at qatlami xato berdi: {fail}"
    assert ok >= 20, f"Kamida 20 ta holat tekshirilishi kerak, hozir {ok}"


def test_sartaroshxonaga_regressiya():
    """'sartaroshxonaga' ichidagi 'osh' bo'lagi 'Oziq-ovqat' deb noto'g'ri
    o'qilmasligi kerak — bu VAZIFA 1'dagi asosiy regressiya testi."""
    cat, score = classify_by_keywords("sartaroshxonaga 25000", "expense")
    assert cat != "🍔 Oziq-ovqat", "'osh' so'z ichida noto'g'ri ushlandi!"
    assert cat == "👗 Kiyim-kechak"
    assert score > 0


def test_no_false_positive_inside_word():
    """Kalit so'z faqat SO'Z BOSHIDA mos kelishi kerak — o'rtada emas."""
    # "restoran" so'zi ichida "tor" yoki boshqa kategoriya so'zi yo'q,
    # lekin umumiy tekshiruv sifatida "ovqat" so'zi so'z boshida emas
    # joylashsa (masalan "sirovqat" kabi mavjud bo'lmagan so'z) mos kelmasligi
    # kerak.
    cat, score = classify_by_keywords("xizmatko'ngilochar", "expense")
    # "ko'ngilochar" so'z o'rtasida emas, boshida joylashgan holatlar mos
    # kelishi kerak, lekin bu yerda oldida "xizmat" harfi bilan bog'langan
    # (bo'sh joysiz) — demak chegaralanmagan, mos kelmasligi kerak.
    assert cat != "🎮 Ko'ngil ochar"


def test_apostrophe_normalization():
    variants = ["to'lov", "to`lov", "to‘lov", "to’lov", "toʻlov", "toʼlov"]
    normalized = {normalize_text(v) for v in variants}
    assert len(normalized) == 1, f"Apostrof variantlari bir xil shaklga kelmadi: {normalized}"


def test_keyword_score_prefers_longer_phrase():
    from services.classifier import _keyword_score
    assert _keyword_score("eski qarz") > _keyword_score("qarz")
    assert _keyword_score("choyxona") > _keyword_score("osh")


# ===================== 2) VAZIFA 2 — YO'NALISH (kirim/chiqim) =====================

DIRECTION_CASES = [
    ("20000 so'm olmaga", "chiqim"),
    ("30000 so'm Mohinurga yo'lkira", "chiqim"),
    ("40000 so'm bog'chaga to'lov qilindi", "chiqim"),
    ("2050000 so'm oylik maosh tushdi", "kirim"),
    ("mijoz 500000 to'ladi", "kirim"),
    ("kitob oldim", "chiqim"),
    ("dori oldim", "chiqim"),
    ("non oldim", "chiqim"),
    ("qizimga o'n ming so'm berdim", "chiqim"),
    ("do'stimdan qarzimni qaytardi", "kirim"),
    ("mashinamni sotdim", "kirim"),
    ("taksiga bordim", "chiqim"),
    ("bozorlik qildim", "chiqim"),
    ("hech narsa aytilmagan matn", "chiqim"),  # shubhali holat -> default chiqim
]


def test_direction_detection():
    ok, fail = 0, []
    for text, expected in DIRECTION_CASES:
        d = detect_direction(text)
        if d == expected:
            ok += 1
        else:
            fail.append((text, expected, d))
    assert not fail, f"Yo'nalish aniqlashda xato: {fail}"
    assert ok == len(DIRECTION_CASES)


def test_direction_default_is_always_expense():
    """Shubhali/signalsiz holatda har doim 'chiqim' — xavfsiz standart."""
    assert detect_direction("tushunarsiz notanish gap") == "chiqim"
    assert detect_direction("") == "chiqim"


def test_fruit_name_is_not_income_signal():
    """'olma oldim' kabi holatlarda mahsulot nomi kirim signaliga aylanib
    ketmasligi kerak — VAZIFA 2'ning asosiy bugi shu edi."""
    assert detect_direction("olmaga yigirma ming berdim") == "chiqim"


# ===================== 3) MATNNI QISMLARGA AJRATISH =====================

def test_split_segments_multi_transaction():
    text = "20000 so'm olmaga, 30000 so'm Mohinurga yo'lkira, 40000 so'm bog'chaga to'lov qilindi"
    segments = split_segments(text)
    assert len(segments) == 3
    assert "olmaga" in segments[0]
    assert "yo'lkira" in segments[1]
    assert "bog'chaga" in segments[2]


def test_split_segments_single_transaction_fallback():
    segments = split_segments("yagona amaliyot matni")
    assert len(segments) == 1


# ===================== 4) LUG'AT HAJMI =====================

def test_dictionary_has_at_least_400_words():
    total = sum(
        len(info["keywords"])
        for defs in (EXPENSE_CATEGORY_DEFS, INCOME_CATEGORY_DEFS)
        for info in defs.values()
    )
    assert total >= 400, f"Lug'at 400 so'zdan kam: {total}"


# ===================== Pytest'siz ishga tushirish uchun =====================

def _run_all():
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    passed, failed = 0, []
    for t in tests:
        try:
            t()
            passed += 1
            print(f"OK   {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} test o'tdi")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
