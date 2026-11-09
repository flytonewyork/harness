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


def test_v4_pages_switch_between_clinics_and_patients():
    clinics, patients = page("v4-clinics.dc.html"), page("v4-patients.dc.html")
    assert re.search(r'aria-current="page"[^>]*>For clinics</a>', clinics)
    assert re.search(r'href="Landing-v4-patients.dc.html"[^>]*>For patients</a>', clinics)
    assert re.search(r'aria-current="page"[^>]*>For patients</a>', patients)
    assert re.search(r'href="Landing-v4-clinics.dc.html"[^>]*>For clinics</a>', patients)


def test_v4_patient_page_keeps_hours_and_points_emergencies_to_999():
    patients = page("v4-patients.dc.html")
    for phrase in ROUND_THE_CLOCK:
        assert phrase not in patients, phrase
    assert "The care line is not an emergency service." in patients
    assert "Not an emergency service. In an emergency, call 999." in patients
    assert "Emergency? Call 999" in patients
    assert "Care team · open 9:00 to 19:00" in patients


def test_v3_menu_fits_one_whatsapp_list_message():
    # WhatsApp list messages hold at most 10 rows in all, and each row title is at most 24 characters.
    titles = MENU_ROW.findall(page("v3.dc.html"))
    assert len(titles) == 10
    assert all(len(t) <= 24 for t in titles)


def test_v3_says_who_finishes_every_menu_option_in_menu_order():
    v3 = page("v3.dc.html")
    rows = TABLE_ROW.findall(v3)
    assert [TABLE_NAME.search(r).group(2) for r in rows] == MENU_ROW.findall(v3)
    for row in rows:
        pills = re.findall(r'white-space: nowrap">([^<]+)</span>', row)
        assert len(pills) == 1 and pills[0] in OWNERS, row
