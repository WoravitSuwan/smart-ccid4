"""หน้าสาธารณะสำหรับประชาชน (A2.2). Insert-only: these views never read case data.

In production this part can run as its own container in the DMZ with a database role that may only INSERT into
the intake tables (see docs/OPERATIONS.md, phase 12).
"""

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import interview, services
from .models import IntakeKind, IntakeLink, IntakeSubmission

MAX_MESSAGE = 2000
MAX_MESSAGES = 80


def active_link(token) -> IntakeLink:
    link = IntakeLink.objects.select_related("owner").filter(token=token).first()
    if not link or not link.active or not link.owner.is_active:
        raise Http404("ลิงก์นี้ยกเลิกแล้ว")
    return link


def kind_or_404(kind):
    if kind not in IntakeKind.values:
        raise Http404
    return kind


def session_key(token, kind):
    return f"intake:{token}:{kind}"


def chat_state(request, token, kind) -> list[dict]:
    msgs = request.session.get(session_key(token, kind))
    if not msgs:
        first = interview.topics_for(kind)[0]
        msgs = [{"role": "bot", "text": first.question, "topic": first.key}]
        request.session[session_key(token, kind)] = msgs
    return msgs


def next_topic(kind, msgs):
    asked = [m["topic"] for m in msgs if m["role"] == "bot" and m.get("topic")]
    answered = {m["topic"] for m in msgs if m["role"] == "user"}
    topics = interview.topics_for(kind)
    current = asked[-1] if asked else None
    if current and current not in answered:
        return next(t for t in topics if t.key == current), False
    for t in topics:
        if t.key not in asked:
            return t, True
    return None, False


def ctx(link, kind, **kw):
    return {"link": link, "kind": kind, "kind_label": IntakeKind(kind).label, "officer": link.owner} | kw


def start(request, token):
    link = active_link(token)
    kind = request.GET.get("kind") if request.GET.get("kind") in IntakeKind.values else ""
    return render(
        request,
        "intake/public/start.html",
        {"link": link, "kind": kind, "kinds": IntakeKind.choices, "pdpa": interview.PDPA_TEXT},
    )


def chat(request, token, kind):
    link, kind = active_link(token), kind_or_404(kind)
    msgs = chat_state(request, token, kind)
    topic, _ = next_topic(kind, msgs)
    return render(request, "intake/public/chat.html", ctx(link, kind, messages_=msgs, finished=topic is None, pdpa=interview.PDPA_TEXT,
                                                          topics=interview.topics_for(kind), max_files=settings.INTAKE_MAX_FILES))  # fmt: skip


@require_POST
def chat_send(request, token, kind):
    active_link(token)
    kind = kind_or_404(kind)
    if not services.allow(f"chat:{services.client_ip(request)}", 60, 600):
        return render(request, "intake/public/_slow_down.html", status=429)
    msgs = chat_state(request, token, kind)
    text = (request.POST.get("text") or "").strip()[:MAX_MESSAGE]
    topic, _ = next_topic(kind, msgs)
    new = []
    if text and topic and len(msgs) < MAX_MESSAGES:
        new.append({"role": "user", "text": text, "topic": topic.key, "at": timezone.now().isoformat()})
        msgs.extend(new)
        nxt, _ = next_topic(kind, msgs)
        if nxt:
            bot = {"role": "bot", "text": nxt.question + (" (ถ้าไม่มี พิมพ์ว่า ไม่มี)" if nxt.optional else ""), "topic": nxt.key}
        else:
            bot = {"role": "bot", "text": "ขอบคุณครับ ได้ข้อมูลครบแล้ว ตรวจทานด้านบน แนบไฟล์ (ถ้ามี) แล้วกดส่งเรื่องได้เลยครับ", "topic": ""}
        msgs.append(bot)
        new.append(bot)
        request.session[session_key(token, kind)] = msgs
    finished = next_topic(kind, msgs)[0] is None
    return render(request, "intake/public/_messages.html", {"new": new, "finished": finished})


@require_POST
def chat_reset(request, token, kind):
    active_link(token)
    request.session.pop(session_key(token, kind_or_404(kind)), None)
    return redirect("intake_public:chat", token=token, kind=kind)


def _submit(request, link, kind, mode, answers, chat_msgs, staff=None):
    if not staff and request.POST.get("consent") != "on":
        messages.error(request, "กรุณาอ่านและติ๊กยินยอมก่อนส่งเรื่อง")
        return None
    if not staff and not services.allow(f"submit:{services.client_ip(request)}", 5, 3600):
        messages.error(request, "ส่งเรื่องถี่เกินไป กรุณารอสักครู่หรือติดต่อสถานีโดยตรง")
        return None
    if not services.allow(f"link:{link.pk}", 200, 86400):
        messages.error(request, "ระบบรับเรื่องเต็มชั่วคราว กรุณาติดต่อสถานีโดยตรง")
        return None
    files, errors = services.check_files(request.FILES.getlist("files"))
    for e in errors:
        messages.warning(request, e)
    return services.create_submission(link, kind, mode, answers, chat_msgs, files, staff=staff)


@require_POST
def chat_submit(request, token, kind):
    link, kind = active_link(token), kind_or_404(kind)
    msgs = chat_state(request, token, kind)
    answers = interview.answers_from_chat(kind, msgs)
    if "reporter" not in answers or "story" not in answers:
        messages.error(request, "ยังขาดข้อมูลผู้แจ้งหรือเหตุการณ์ ตอบคำถามให้ครบก่อนส่ง")
        return redirect("intake_public:chat", token=token, kind=kind)
    sub = _submit(request, link, kind, IntakeSubmission.Mode.CHAT, answers, msgs)
    if sub is None:
        return redirect("intake_public:chat", token=token, kind=kind)
    request.session.pop(session_key(token, kind), None)
    return redirect("intake_public:done", track=sub.track_token)


def form_answers(post, kind) -> tuple[dict, list[str]]:
    answers, missing = {}, []
    for t in interview.topics_for(kind):
        vals = {f.key: (post.get(f"{t.key}-{f.key}") or "").strip()[:MAX_MESSAGE] for f in t.fields}
        for f in t.fields:
            if f.required and not vals[f.key]:
                missing.append(f.label)
        vals = {k: v for k, v in vals.items() if v}
        if vals:
            answers[t.key] = vals
    return answers, missing


def step_form(request, token, kind, staff=None, link=None):
    link, kind = link or active_link(token), kind_or_404(kind)
    values = {}
    if request.method == "POST":
        answers, missing = form_answers(request.POST, kind)
        values = request.POST
        if missing:
            messages.error(request, "กรอกให้ครบ: " + ", ".join(missing))
        else:
            sub = _submit(request, link, kind, IntakeSubmission.Mode.FORM, answers, [], staff=staff)
            if sub is not None:
                if staff:
                    messages.success(request, f"ส่งเรื่องแล้ว เลขอ้างอิง {sub.reference} · ระบบกำลังตั้งเป็นคดี")
                    return redirect("intake:index")
                return redirect("intake_public:done", track=sub.track_token)
    template = "intake/staff_form.html" if staff else "intake/public/form.html"
    steps = [
        (t, [(f, f"{t.key}-{f.key}", values.get(f"{t.key}-{f.key}", "")) for f in t.fields]) for t in interview.topics_for(kind)
    ]
    return render(request, template, ctx(link, kind, steps=steps, pdpa=interview.PDPA_TEXT, staff=staff,
                                         max_files=settings.INTAKE_MAX_FILES, active="intake"))  # fmt: skip


def done(request, track):
    sub = get_object_or_404(IntakeSubmission, track_token=track)
    return render(request, "intake/public/done.html", {"sub": sub})


def track(request, track):
    from . import tracking

    obj, case, state = tracking.resolve(track)
    if state == "missing":
        raise Http404
    if state == "expired":
        return render(request, "intake/public/track.html", {"expired": True, "reference": obj.reference}, status=410)
    if isinstance(obj, IntakeSubmission):
        steps, reference = services.track_status(obj), obj.reference
    else:
        steps, reference = tracking.steps(case, obj.created_at), obj.reference
    resp = render(
        request,
        "intake/public/track.html",
        {"steps": steps, "reference": reference, "expires_at": getattr(obj, "expires_at", None)},
    )
    resp["X-Robots-Tag"] = "noindex, nofollow"
    return resp
