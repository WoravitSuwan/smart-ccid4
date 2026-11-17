import re
from pathlib import Path

import pytest
from django.conf import settings

from core import thai

ROOT = Path(settings.BASE_DIR)


def _luminance(hex_color):
    h = hex_color.lstrip("#")[:6]
    rgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a, b):
    la, lb = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens(theme):
    css = (ROOT / "static/css/tokens.css").read_text()
    block = re.search(
        r':root, \[data-theme="dark"\] \{(.*?)\}' if theme == "dark" else r'\[data-theme="light"\] \{(.*?)\}', css, re.S
    ).group(1)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6,8})", block))


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_pairs_meet_wcag_aa(theme):
    t = tokens(theme)
    for fg, bg in [
        ("text", "surface"),
        ("text", "bg"),
        ("text-muted", "surface"),
        ("on-primary", "primary"),
        ("primary", "surface"),
        ("danger", "surface"),
        ("success", "surface"),
        ("warning-ink", "surface"),
        ("accent-ink", "surface"),
        ("ai", "surface"),
    ]:
        assert contrast(t[fg], t[bg]) >= 4.5, f"{theme}: {fg} on {bg} = {contrast(t[fg], t[bg]):.2f}"


def test_no_external_resources_in_templates_or_own_static():
    pattern = re.compile(r"""(?:src|href|url)\s*=?\s*\(?["']?(https?:)?//""", re.I)
    files = (
        list((ROOT / "templates").rglob("*.html")) + list((ROOT / "static/js").rglob("*.js")) + [ROOT / "static/src/input.css"]
    )
    offenders = [str(f) for f in files if pattern.search(f.read_text())]
    assert offenders == []


@pytest.mark.django_db
def test_styleguide_shows_both_themes(client_inv):
    html = client_inv.get("/styleguide/").content.decode()
    assert 'data-theme="dark"' in html and 'data-theme="light"' in html and "ร่างโดย AI – รอตรวจ" in html


@pytest.mark.django_db
def test_menu_marks_unbuilt_items(client, make_user):
    client.force_login(make_user(role="supervisor"))
    html = client.get("/").content.decode()
    assert "ภาพรวม" in html and "เร็ว ๆ นี้" in html  # แดชบอร์ดหน่วยงาน arrives in phase 11
    assert 'href="/cyber/network/"' in html  # เครือข่ายคดี is built (phase 9)


def test_thai_formatting():
    from datetime import date

    assert thai.format_date(date(2026, 11, 25)) == "25 พ.ย. 2569"
    assert thai.format_date(date(2026, 11, 25), "doc") == "๒๕ เดือน พฤศจิกายน พ.ศ. ๒๕๖๙"
    assert thai.thai_words_to_int("สองแสนแปดหมื่นแปดพัน") == 288000
    assert thai.mask_citizen_id("3000000000456") == "3-xxxx-xxxxx-45-6"
