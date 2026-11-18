"""create_officer: ภาษาไทยจากหน้าต่างคำสั่ง (Backspace, code page 874) ต้องบันทึกได้ และรหัสผ่านต้องเป็นแป้นอังกฤษ."""

import os

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.management.commands import create_officer as cmd
from accounts.models import User

PASSWORD = "Strong-pass-2569"


def from_thai_console(text: str) -> str:
    """สิ่งที่ Python ใน Linux ได้รับเมื่อคอนโซล Windows ภาษาไทยส่งข้อความเป็น cp874."""
    return text.encode("cp874").decode("utf-8", "surrogateescape")


def feed(monkeypatch, typed: list[str], secrets: list[str]):
    typed, secrets = iter(typed), iter(secrets)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    monkeypatch.setattr(cmd.getpass, "getpass", lambda prompt="": next(secrets))


@pytest.mark.parametrize("text", ["ชัชวาลย์", "ร.ต.ท.", "รอง สว.(สอบสวน)", "นาย ก 2569"])
def test_repair_console_text_restores_thai(text):
    garbled = from_thai_console(text)
    assert any("\udc80" <= ch <= "\udcff" for ch in garbled)
    assert cmd.repair_console_text(garbled) == text
    assert cmd.repair_console_text(text) == text  # UTF-8 input is untouched


@pytest.mark.django_db
def test_interactive_cp874_console_creates_account_that_can_log_in(monkeypatch, capsys):
    typed = ["officer1", "ร.ต.ท.", "สมมติ", "ทดสอบ", "พนักงานสอบสวน"]
    feed(monkeypatch, [from_thai_console(t) for t in typed], [PASSWORD, PASSWORD])
    call_command("create_officer")
    u = User.objects.get(username="officer1")
    assert (u.rank, u.first_name, u.last_name, u.position) == ("ร.ต.ท.", "สมมติ", "ทดสอบ", "พนักงานสอบสวน")
    assert u.check_password(PASSWORD) and u.is_active


@pytest.mark.django_db
def test_password_rules_are_explained_and_retried(monkeypatch, capsys):
    thai_password = from_thai_console("รหัสผ่านภาษาไทยยาวมาก")
    feed(
        monkeypatch,
        ["ผู้ใช้", "officer2", "", "สมมติ", "ทดสอบ", ""],  # Thai username is refused, then an English one
        ["short", thai_password, PASSWORD, "different-pass-1", PASSWORD, PASSWORD],
    )
    call_command("create_officer")
    err = capsys.readouterr().err
    assert "ภาษาอังกฤษ" in err and "สั้นเกินไป" in err and "ไม่ตรงกัน" in err
    assert User.objects.get(username="officer2").check_password(PASSWORD)


@pytest.mark.django_db
def test_arguments_and_cancel(monkeypatch):
    call_command(
        "create_officer", username="officer3", rank="", first_name=from_thai_console("สมมติ"), last_name="ทดสอบ",
        position="", password=PASSWORD,
    )  # fmt: skip
    u = User.objects.get(username="officer3")
    assert (u.rank, u.first_name, u.position) == ("", "สมมติ", "")
    with pytest.raises(CommandError, match="12"):
        call_command("create_officer", username="officer4", rank="", first_name="ก", last_name="ข", position="", password="short")
    with pytest.raises(CommandError, match="มีชื่อผู้ใช้"):
        call_command("create_officer", username="OFFICER3", first_name="ก", last_name="ข", password=PASSWORD)

    def cancelled(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", cancelled)
    with pytest.raises(CommandError, match="ยกเลิก"):
        call_command("create_officer")
    assert not User.objects.filter(username="officer4").exists()


def test_repair_drops_leftovers_of_backspace():
    """เทอร์มินัลที่ไม่มี IUTF8 ลบอักษรไทยแค่ไบต์เดียว เหลือเศษ 2 ไบต์ — ต้องได้ข้อความตามที่เห็นบนจอ."""
    typed = "ญะ".encode() + "ก".encode()[:2] + "วิญชาญ".encode()
    garbled = typed.decode("utf-8", "surrogateescape")
    assert "\udce0" in garbled
    assert cmd.repair_console_text(garbled) == "ญะวิญชาญ"


def test_utf8_line_editing_turns_on_iutf8_and_restores(monkeypatch):
    termios = pytest.importorskip("termios")
    pty = pytest.importorskip("pty")
    leader, follower = pty.openpty()
    try:
        attrs = termios.tcgetattr(follower)
        attrs[0] &= ~termios.IUTF8
        termios.tcsetattr(follower, termios.TCSANOW, attrs)
        monkeypatch.setattr("sys.stdin", open(follower, closefd=False))
        with cmd.utf8_line_editing():
            assert termios.tcgetattr(follower)[0] & termios.IUTF8
        assert not termios.tcgetattr(follower)[0] & termios.IUTF8
    finally:
        os.close(leader)
        os.close(follower)
