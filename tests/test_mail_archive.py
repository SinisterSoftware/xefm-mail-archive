from email.message import EmailMessage
import os
from types import SimpleNamespace

from mail_archive import logic


def build_eml(path):
    msg = EmailMessage()
    msg["From"] = "a@example.com"
    msg["To"] = "b@example.com"
    msg["Subject"] = "テスト件名"
    msg.set_content("こんにちは本文", charset="utf-8")
    msg.add_attachment(b"payload", maintype="application", subtype="octet-stream", filename="日本語.bin")
    msg.add_attachment(b"dup", maintype="application", subtype="octet-stream", filename="日本語.bin")
    path.write_bytes(msg.as_bytes())


def test_charset_fix_prefers_cp932():
    assert logic.CHARSET_FIX["shift_jis"] == "cp932"
    assert logic.decode_text("①".encode("cp932"), "Shift_JIS") == "①"
    assert logic.decode_text(b"abc", "no-such-charset") == "abc"


def test_fix_8bit_and_safe_name():
    raw = "日本".encode("cp932").decode("ascii", "surrogateescape")
    assert logic.fix_8bit(raw) == "日本"
    assert logic.fix_8bit("plain") == "plain"
    assert logic.safe_name("a/b\\c:d.txt") == "c_d.txt"
    assert logic.safe_name("") == "attachment"


def test_eml_virtual_folder(tmp_path):
    eml = tmp_path / "m.eml"
    build_eml(eml)
    root = logic.EmlPathImpl(f"eml://{eml}#")
    names = sorted(p.name for p in root.iterdir())
    assert "header.txt" in names and "attach" in names
    assert any(n.startswith("message000(utf-8)") for n in names)
    assert root.is_dir() and root.name == "m.eml"

    header = logic.EmlPathImpl(f"eml://{eml}#header.txt")
    assert "Subject: テスト件名" in header.open("r").read()
    body = next(n for n in names if n.startswith("message"))
    assert "こんにちは本文" in logic.EmlPathImpl(f"eml://{eml}#{body}").open("r").read()

    attach = logic.EmlPathImpl(f"eml://{eml}#attach")
    assert attach.is_dir()
    assert sorted(p.name for p in attach.iterdir()) == ["日本語.bin", "日本語_2.bin"]
    leaf = logic.EmlPathImpl(f"eml://{eml}#attach/日本語.bin")
    assert leaf.open("rb").read() == b"payload"
    assert leaf.stat().st_size == 7
    assert not logic.EmlPathImpl(f"eml://{eml}#nothing").exists()


def test_read_only(tmp_path):
    eml = tmp_path / "m.eml"
    build_eml(eml)
    import pytest

    with pytest.raises(OSError):
        logic.EmlPathImpl(f"eml://{eml}#header.txt").write_text("x")


def test_msg_format_message():
    class Msg:
        sender, to, cc, bcc, date, subject = "s@x", "t@x", "", "", "2030-01-01", "Hi"
        body = "line1\r\nline2"
        htmlBody = None

    text = logic.format_message(Msg(), ["a.txt"])
    assert "Subject: Hi" in text and "Attach : 1" in text and "  - a.txt" in text
    assert "line1\nline2" in text and "Bcc" not in text


def test_schemes():
    assert logic.EML_SCHEME["class"] is logic.EmlPathImpl
    assert logic.MSG_SCHEME["class"] is logic.MsgPathImpl
    assert logic.MsgPathImpl.SCHEME == "msg"


def test_open_mail_archive_routes_supported_files_and_ignores_others(tmp_path):
    class Pane:
        path = str(tmp_path)

        def __init__(self):
            self.visited = []

        def cd(self, path):
            self.visited.append(path)
            self.path = path

    pane = Pane()
    ctx = SimpleNamespace(pane=pane)
    eml = tmp_path / "mail.eml"
    msg = tmp_path / "mail.msg"

    assert logic.open_mail_archive(ctx, SimpleNamespace(path=eml)) is True
    assert pane.path == f"eml://{eml.absolute()}#"
    assert logic.open_mail_archive(ctx, SimpleNamespace(path=msg)) is True
    assert pane.path == f"msg://{msg.absolute()}#"
    assert logic.open_mail_archive(ctx, SimpleNamespace(path=tmp_path / "notes.txt")) is False
    assert len(pane.visited) == 2


def test_open_eml_is_idempotent_for_existing_external_viewer_hook(tmp_path):
    eml = tmp_path / "mail.eml"
    uri = f"eml://{eml.absolute()}#"

    class Pane:
        path = os.path.normcase(os.path.normpath(uri))

        def cd(self, _path):
            raise AssertionError("already-open archive should not be entered twice")

    assert logic.open_eml(SimpleNamespace(pane=Pane()), eml) is True


def test_open_focused_mail_archive_ignores_non_files_and_routes_mail(tmp_path):
    class Pane:
        path = str(tmp_path)

        def __init__(self, focused):
            self.focused = focused
            self.visited = []

        def cd(self, path):
            self.visited.append(path)
            self.path = path

    eml = tmp_path / "focused.eml"
    pane = Pane(SimpleNamespace(name=eml.name, is_dir=False, path=eml))
    assert logic.open_focused_mail_archive(SimpleNamespace(pane=pane)) is True
    assert pane.visited == [f"eml://{eml.absolute()}#"]

    for focused in (
        None,
        SimpleNamespace(name="..", is_dir=True, path=tmp_path),
        SimpleNamespace(name="folder", is_dir=True, path=tmp_path),
        SimpleNamespace(name="notes.txt", is_dir=False, path=tmp_path / "notes.txt"),
    ):
        pane = Pane(focused)
        assert logic.open_focused_mail_archive(SimpleNamespace(pane=pane)) is False
        assert pane.visited == []
