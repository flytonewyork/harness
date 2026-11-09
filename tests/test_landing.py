"""The doctors' landing page, saved from its design canvas (spec 002, `demo/landing/`).

v2 is the version to go back to, so it must not change. v3 carries the red-team fixes: the care line keeps the
clinic's hours, and its WhatsApp menu fits what a WhatsApp list message can hold.
"""
import hashlib
import re
from pathlib import Path

import pytest

LANDING = Path(__file__).resolve().parents[1] / "demo" / "landing"
V2_SHA256 = "0a56829ee10d110a68c62ec440b04b446f361bbd0c06f01124318cad28566835"

MENU_ROW = re.compile(
    r'<li style="display: flex; align-items: center; gap: 12px; padding: 7px 16px">'
    r'<span [^>]*><span style="font-size: 14.5px; font-weight: 600">([^<]+)</span>'
)
TABLE_ROW = re.compile(r'<div role="row" class="fd-row".*?</div>', re.S)
TABLE_NAME = re.compile(r'class="fd-name"[^>]*><span [^>]*>([^<]+)</span><span [^>]*>([^<]+)</span>')
OWNERS = {"Care team", "Staff", "You"}


def page(name):
    return (LANDING / name).read_text(encoding="utf-8")


def test_v2_is_kept_exactly_as_saved():
    assert hashlib.sha256((LANDING / "v2.dc.html").read_bytes()).hexdigest() == V2_SHA256


ROUND_THE_CLOCK = ("24/7", "replies in minutes", "never closes", "Nothing sent without your approval")


@pytest.mark.parametrize("name", ["v3.dc.html", "v4-clinics.dc.html"])
def test_clinic_pages_keep_opening_hours_and_promise_no_round_the_clock_replies(name):
    text = page(name)
    for phrase in ROUND_THE_CLOCK:
        assert phrase not in text, phrase
    assert "Open when you're open." in text
    assert text.count("Nothing clinical sent without your approval") == 2  # the hero and the sign-up box
    assert text.count("Care team · open 9:00 to 19:00") == 3  # every phone mock shows the hours


# file in the repo -> (its name on the canvas, its language, its audience)
V4 = {
    "v4-clinics.dc.html": ("Landing-v4-clinics.dc.html", "en", "clinics"),
    "v4-patients.dc.html": ("Landing-v4-patients.dc.html", "en", "patients"),
    "v4-clinics-zh.dc.html": ("Landing-v4-clinics-zh.dc.html", "zh", "clinics"),
    "v4-patients-zh.dc.html": ("Landing-v4-patients-zh.dc.html", "zh", "patients"),
}
AUDIENCE_LABEL = {("en", "clinics"): "For clinics", ("en", "patients"): "For patients",
                  ("zh", "clinics"): "診所專區", ("zh", "patients"): "病人專區"}
LANGUAGE_LABEL = {"en": "English", "zh": "中文"}


def canvas_name(lang, audience):
    return next(c for c, l, a in V4.values() if l == lang and a == audience)


@pytest.mark.parametrize("name", sorted(V4))
def test_v4_pages_switch_audience_within_their_language(name):
    _, lang, audience = V4[name]
    other = "patients" if audience == "clinics" else "clinics"
    text = page(name)
    assert re.search(rf'aria-current="page"[^>]*>{AUDIENCE_LABEL[lang, audience]}</a>', text)
    assert re.search(rf'href="{canvas_name(lang, other)}"[^>]*>{AUDIENCE_LABEL[lang, other]}</a>', text)


@pytest.mark.parametrize("name", sorted(V4))
def test_v4_pages_switch_language_and_default_to_english(name):
    _, lang, audience = V4[name]
    other = "zh" if lang == "en" else "en"
    text = page(name)
    assert text.count('aria-label="Language"' if lang == "en" else 'aria-label="語言"') == 1
    assert re.search(rf'aria-current="true"[^>]*>{LANGUAGE_LABEL[lang]}</a>', text)
    assert re.search(rf'href="{canvas_name(other, audience)}"[^>]*>{LANGUAGE_LABEL[other]}</a>', text)
    assert ('<html lang="zh-HK">' if lang == "zh" else '<html lang="en">') in text


def test_v4_patient_page_keeps_hours_and_points_emergencies_to_999():
    patients = page("v4-patients.dc.html")
    for phrase in ROUND_THE_CLOCK:
        assert phrase not in patients, phrase
    assert "The care line is not an emergency service." in patients
    assert "Not an emergency service. In an emergency, call 999." in patients
    assert "Emergency? Call 999" in patients
    assert "Care team · open 9:00 to 19:00" in patients
    assert "In an emergency, call 999 or go to A&amp;E." in patients  # the hero line phones keep


def test_v4_chinese_patient_page_keeps_hours_and_points_emergencies_to_999():
    patients = page("v4-patients-zh.dc.html")
    for phrase in ROUND_THE_CLOCK:
        assert phrase not in patients, phrase
    assert "跟進服務並非緊急服務。" in patients
    assert "本服務並非緊急服務。如遇緊急情況，請致電 999。" in patients
    assert "如遇緊急情況，請致電 999 或前往急症室。" in patients
    assert "跟進團隊 · 服務時間 9:00 至 19:00" in patients


@pytest.mark.parametrize("name", ["v3.dc.html", "v4-clinics.dc.html", "v4-clinics-zh.dc.html"])
def test_menus_fit_one_whatsapp_list_message(name):
    # WhatsApp list messages hold at most 10 rows in all, and each row title is at most 24 characters.
    titles = MENU_ROW.findall(page(name))
    assert len(titles) == 10
    assert all(len(t) <= 24 for t in titles)


def test_v3_says_who_finishes_every_menu_option_in_menu_order():
    v3 = page("v3.dc.html")
    rows = TABLE_ROW.findall(v3)
    assert [TABLE_NAME.search(r).group(2) for r in rows] == MENU_ROW.findall(v3)
    for row in rows:
        pills = re.findall(r'white-space: nowrap">([^<]+)</span>', row)
        assert len(pills) == 1 and pills[0] in OWNERS, row


def x_dc_body(text):
    return text.split("<x-dc>", 1)[1].split("</x-dc>", 1)[0]


@pytest.mark.parametrize("name", ["v4-clinics.dc.html", "v4-patients.dc.html"])
def test_english_v4_pages_are_in_english_including_the_whatsapp_examples(name):
    # The only Chinese left is the switch's own label.
    assert re.findall(r"[\u4e00-\u9fff]+", x_dc_body(page(name))) == ["中文"]


def test_the_language_switch_changes_the_whatsapp_examples_too():
    pairs = [("v4-clinics.dc.html", "Hi Mr Chan, Dr Lee has looked at last week's blood test",
              "v4-clinics-zh.dc.html", "陳生你好，李醫生已經睇咗你上星期驗血嘅報告"),
             ("v4-patients.dc.html", "Ms Kwok, is your cough any better?",
              "v4-patients-zh.dc.html", "郭小姐，咳好啲未？")]
    for en_name, en_line, zh_name, zh_line in pairs:
        assert en_line in page(en_name) and zh_line not in page(en_name)
        assert zh_line in page(zh_name) and en_line not in page(zh_name)
