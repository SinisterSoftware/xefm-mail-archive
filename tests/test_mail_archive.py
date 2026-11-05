from email.message import EmailMessage

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
