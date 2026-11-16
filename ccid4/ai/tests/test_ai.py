"""Phase 5: AI adapter, fake/ollama backends, case-page AI actions, rights notice, confirmation, dika search."""

import io
import json
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from ai import embed, llm, schema
from ai.context import case_context
from ai.models import AICall
from audit.models import AuditLog
from cases.models import CircumstanceVersion
from cases.tests.factories import CaseFactory, PartyFactory, PersonFactory
from deadlines.models import Task
from dika import services as dika
from dika.models import PinnedRuling
from parties.models import Statement

SAMPLE = Path(__file__).resolve().parents[2] / "sample_data" / "dika_sample.json"


@pytest.fixture
def case(investigator):
    c = CaseFactory(
        owner=investigator, offense="", law_sections="", max_penalty_months=None, location="กรุงเทพมหานคร",
        circumstances="ผู้ต้องหาหลอกขายโทรศัพท์ทางออนไลน์ ผู้กล่าวหาโอนเงิน 12000 บาท แล้วไม่ได้รับสินค้า", damage_amount=12000,
    )  # fmt: skip
    PartyFactory(case=c, role="accuser", person=PersonFactory(first_name="ผู้แจ้ง", citizen_id="3000000000456"))
    return c


@pytest.fixture
def suspect(case):
    return PartyFactory(case=case, role="suspect", person=PersonFactory(first_name="ผู้ต้องหา"), arrived_at=timezone.now())


# ---------------------------------------------------------------- adapter
def test_every_task_has_a_prompt_and_schema():
    for task in ("analyze_charges", "summarize_case", "draft_statement", "check_consistency"):
        text, sch, version = llm.prompt_files(task)
        assert "$context" in text and sch["type"] == "object" and len(version) == 10


def test_schema_checker():
    sch = {
        "type": "object",
        "required": ["a"],
        "properties": {"a": {"type": "integer", "minimum": 0}, "b": {"type": "string", "enum": ["x"]}},
    }
    assert schema.errors({"a": 1, "b": "x"}, sch) == []
    assert schema.errors({"a": -1}, sch) and schema.errors({"b": "y"}, sch) and schema.errors({"a": True}, sch)


@pytest.mark.django_db
def test_fake_charges_and_call_log_without_content(case, investigator):
    out = llm.complete_json("analyze_charges", case_context(case), user=investigator, case=case)
    assert out["max_penalty_months"] == 60 and "ม.343" in out["law_sections"] and out["court_type"] == "criminal"
    call = AICall.objects.get()
    assert call.ok and call.task == "analyze_charges" and call.backend == "fake" and call.user == investigator
    stored = " ".join(str(getattr(call, f.name)) for f in AICall._meta.fields)
    assert "โทรศัพท์" not in stored and "12000" not in stored  # no case content in the log


@pytest.mark.django_db
def test_retry_once_then_fail(monkeypatch):
    answers = iter([{"summary": 5}, {"summary": "ok"}])
    monkeypatch.setattr(llm.FakeLLM, "__call__", lambda self, *a: next(answers))
    assert llm.complete_json("summarize_case", {})["summary"] == "ok"
    assert AICall.objects.get().attempts == 2
    monkeypatch.setattr(llm.FakeLLM, "__call__", lambda self, *a: {"wrong": 1})
    with pytest.raises(llm.AIError, match="2 ครั้ง"):
        llm.complete_json("summarize_case", {})
    assert not AICall.objects.latest("pk").ok


@pytest.mark.django_db
def test_ollama_backend_sends_schema_to_local_server(settings, monkeypatch):
    settings.AI_BACKEND, settings.OLLAMA_MODEL, settings.OLLAMA_URL = "ollama", "typhoon-test", "http://ollama:11434"
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return io.BytesIO(json.dumps({"message": {"content": json.dumps({"summary": "สรุป"})}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert llm.complete_json("summarize_case", {"คดี": {}})["summary"] == "สรุป"
    assert seen["url"] == "http://ollama:11434/api/chat"
    assert seen["body"]["model"] == "typhoon-test" and seen["body"]["format"]["required"] == ["summary"]
    assert AICall.objects.get().model == "typhoon-test"


@pytest.mark.django_db
def test_ollama_down_or_unconfigured_gives_thai_message(settings, monkeypatch):
    import urllib.error

    settings.AI_BACKEND, settings.OLLAMA_MODEL = "ollama", ""
    with pytest.raises(llm.AIError, match="OLLAMA_MODEL"):
        llm.complete_json("summarize_case", {})
    settings.OLLAMA_MODEL = "m"

    def boom(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(llm.AIError, match="ติดต่อเครื่อง AI"):
        llm.complete_json("summarize_case", {})


@pytest.mark.django_db
def test_ai_context_has_no_citizen_id(case):
    assert "3000000000456" not in json.dumps(case_context(case), ensure_ascii=False)


def test_fake_embeddings_are_stable_and_meaningful(settings):
    a, b, c = embed.embed(["หลอกขายสินค้าออนไลน์", "หลอกขายสินค้าทางออนไลน์", "ขับรถชนคนบาดเจ็บ"])
    assert len(a) == settings.AI_EMBED_DIM and abs(sum(x * x for x in a) - 1) < 1e-9
    dot = lambda x, y: sum(i * j for i, j in zip(x, y, strict=True))  # noqa: E731
    assert dot(a, b) > dot(a, c)
    assert embed.embed(["หลอกขายสินค้าออนไลน์"])[0] == a


# ---------------------------------------------------------------- case page actions
@pytest.mark.django_db
def test_charges_view_offers_values_to_apply(client_inv, case):
    r = client_inv.post(reverse("ai:charges", args=[case.pk]))
    html = r.content.decode()
    assert r.status_code == 200 and "ร่างโดย AI – รอตรวจ" in html and "data-apply-ai" in html and "c-max_penalty_months" in html
    case.refresh_from_db()
    assert case.offense == ""  # nothing saved until the officer applies and saves


@pytest.mark.django_db
def test_summary_saved_as_ai_version(client_inv, case):
    from cases.tests.test_case_page import page_data

    r = client_inv.post(reverse("ai:summary", args=[case.pk]))
    assert "พฤติการณ์แห่งคดีที่ AI เรียบเรียง" in r.content.decode()
    from ai.services import summarize

    text = summarize(case, None)
    url = reverse("cases:detail", args=[case.pk])
    data = page_data(client_inv.get(url))
    data["c-circumstances"], data["circ_ai"] = text, text
    client_inv.post(url, data)
    assert CircumstanceVersion.objects.get(case=case).source == "ai"
    data = page_data(client_inv.get(url))
    data["c-circumstances"] = text + " แก้เอง"
    client_inv.post(url, data)
    assert list(CircumstanceVersion.objects.filter(case=case).values_list("version", "source")) == [(2, "manual"), (1, "ai")]


@pytest.mark.django_db
def test_ai_draft_label_then_confirm_acceptance_6(client_inv, case, suspect, investigator):
    r = client_inv.post(reverse("ai:draft", args=[case.pk, suspect.pk]), {"plea": "deny"})
    assert r.status_code == 204 and r["HX-Refresh"] == "true"
    st = suspect.current_statement
    assert st.source == "ai" and st.needs_review and "ปฏิเสธ" in st.text
    html = client_inv.get(reverse("cases:detail", args=[case.pk])).content.decode()
    assert "ร่างโดย AI – รอตรวจ" in html and "ยืนยันคำให้การ" in html
    client_inv.post(reverse("ai:confirm", args=[case.pk, st.pk]))
    st.refresh_from_db()
    assert not st.needs_review and st.confirmed_by == investigator and st.confirmed_at
    assert AuditLog.objects.filter(action="confirm", object_type="statement").exists()
    assert Task.objects.filter(case=case, title__startswith="ตรวจและยืนยันคำให้การ").exists()


@pytest.mark.django_db
def test_assistant_cannot_confirm(client, case, suspect, make_user, investigator):
    assistant = make_user(role="assistant")
    case.shared_with.add(assistant)
    st = Statement.objects.create(party=suspect, version=1, text="x", source="ai", created_by=investigator)
    client.force_login(assistant)
    assert client.post(reverse("ai:confirm", args=[case.pk, st.pk])).status_code == 403


@pytest.mark.django_db
def test_rights_notice_records_plea_and_drafts(client_inv, case, suspect):
    from deadlines.services import sync_rule_tasks

    sync_rule_tasks(case)
    assert not Task.objects.get(case=case, rule_key=f"rights:{suspect.pk}").done_at
    url = reverse("ai:rights", args=[case.pk, suspect.pk])
    client_inv.post(url, {"plea": "confess", "informed_at": "2026-11-16T10:30", "draft": "on"})  # not ticked
    suspect.refresh_from_db()
    assert suspect.rights_informed_at is None
    client_inv.post(url, {"informed": "on", "plea": "confess", "informed_at": "2026-11-16T10:30", "draft": "on"})
    suspect.refresh_from_db()
    assert suspect.plea == "confess" and timezone.localtime(suspect.rights_informed_at).hour == 10
    assert suspect.current_statement.source == "ai" and "รับสารภาพ" in suspect.current_statement.text
    assert Task.objects.get(case=case, rule_key=f"rights:{suspect.pk}").done_at


@pytest.mark.django_db
def test_witness_consistency_flags_numbers(client_inv, case, investigator):
    w = PartyFactory(case=case, role="witness", person=PersonFactory(first_name="พยาน"))
    Statement.objects.create(party=w, version=1, text="ข้าฯ เห็นผู้กล่าวหาโอนเงิน 15000 บาท", created_by=investigator)
    html = client_inv.post(reverse("ai:consistency", args=[case.pk, w.pk])).content.decode()
    assert "15000" in html and "ไม่ตรงกัน" in html


@pytest.mark.django_db
def test_ai_actions_need_edit_rights(client, case, suspect, make_user):
    client.force_login(make_user(role="commander"))
    for name, args in (("ai:charges", [case.pk]), ("ai:summary", [case.pk]), ("ai:draft", [case.pk, suspect.pk])):
        assert client.post(reverse(name, args=args)).status_code == 403


# ---------------------------------------------------------------- dika (A4.7)
@pytest.fixture
def rulings(db):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    dika.import_rows(data["rulings"], "ข้อมูลจำลอง")


def test_sample_rulings_are_marked_fictional():
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert "ข้อมูลจำลอง" in data["_note"] and all(r["number"].startswith("ตัวอย่าง-") for r in data["rulings"])


@pytest.mark.django_db
def test_import_is_idempotent(rulings):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert dika.import_rows(data["rulings"], "x") == (0, len(data["rulings"]))


@pytest.mark.django_db
def test_hybrid_search_ranks_related_rulings(rulings):
    hits = dika.search("หลอกขายสินค้าออนไลน์ โอนเงินแล้วไม่ส่งของ", "ป.อ. ม.343")
    assert hits and "343" in hits[0].ruling.sections and 0 < hits[0].percent <= 100
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)
    only = dika.search("ม.337", only_sections=True)
    assert only and all("337" in h.ruling.sections for h in only)
    assert dika.sections_in("ป.อ. มาตรา ๓๔๓ และ ม.14(1)") == {"343", "14(1)"}


@pytest.mark.django_db
def test_dika_tab_pin_and_report(client_inv, case, rulings, investigator):
    case.offense, case.law_sections = "ฉ้อโกงประชาชน", "ป.อ. ม.343"
    case.save()
    html = client_inv.get(reverse("dika:search", args=[case.pk])).content.decode()
    assert "ค้นอัตโนมัติ" in html and "ปักหมุดเข้าคดี" in html
    target = dika.search("ฉ้อโกงประชาชน", "ม.343")[0].ruling
    client_inv.post(reverse("dika:pin", args=[case.pk, target.pk]))
    assert PinnedRuling.objects.filter(case=case, ruling=target).exists()
    from documents.context import build

    assert target.number in build(case)["ฎีกาที่อ้าง"]
    client_inv.post(reverse("dika:pin", args=[case.pk, target.pk]))  # toggles off
    assert not PinnedRuling.objects.exists()
    html = client_inv.get(reverse("dika:search", args=[case.pk]), {"dika_q": "บัญชีม้า"}).content.decode()
    assert "ผลการค้น" in html and "บัญชีม้า" in html
