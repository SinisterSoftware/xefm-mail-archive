# -*- coding: utf-8 -*-
"""
.eml / .msg (Outlook) を書庫のように扱うためのモジュール。

eml://<.emlの絶対パス>#  /  msg://<.msgの絶対パス>#  の中身:
    header.txt / message*.txt|html   (.eml) ヘッダと本文
    message.txt                      (.msg) ヘッダ・添付ファイル名の一覧・本文
    attach/                          添付ファイル

.msg の読み取りには extract-msg が必要 (requirements.txt に記載。Sync Python Packages で導入)。

config.py (パッケージ化後は不要。拡張の path_schemes が eml / msg を登録する):
    from mail_archive import EmlPathImpl, MsgPathImpl
    PATH_SCHEMES = {
        'eml': {'class': EmlPathImpl, 'override': True},
        'msg': {'class': MsgPathImpl},
    }
Enter での cd は external_viewer.py の EXTERNAL_VIEWERS が open_eml / open_msg を呼ぶ。
"""

import email
import email.header
import email.message
import email.policy
import errno
import io
import os
import re

from xefm.path_base import ReadOnlyPathImpl, UriStatResult

# 日本語メールで Python 標準の codec だと機種依存文字 (①㈱ など) が欠ける指定を上位互換に読み替える
CHARSET_FIX = {
    'shift_jis': 'cp932', 'shift-jis': 'cp932', 'sjis': 'cp932', 'x-sjis': 'cp932',
    'windows-31j': 'cp932', 'ms932': 'cp932', 'iso-2022-jp': 'iso2022_jp_ext',
    'euc-jp': 'euc_jp', 'x-euc-jp': 'euc_jp', 'gb2312': 'gb18030', 'gbk': 'gb18030',
}


def decode_text(data, charset):
    charset = (charset or 'utf-8').lower()
    try:
        return data.decode(CHARSET_FIX.get(charset, charset), errors='replace')
    except LookupError:
        return data.decode('utf-8', errors='replace')


def fix_8bit(text):
    """ヘッダに生で入っていた8bit文字 (surrogateescape で読まれたもの) を復号し直す。"""
    try:
        data = text.encode('ascii', 'surrogateescape')
    except UnicodeEncodeError:
        return text
    if data.isascii():
        return text
    for enc in ('utf-8', 'cp932', 'euc_jp', 'iso2022_jp_ext'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode('utf-8', errors='replace')


def _param_text(value):
    """get_params の値 (RFC 2231 なら (charset, lang, 文字列)) を、=?..?= も含めて文字列にする。"""
    if isinstance(value, tuple):
        charset, _lang, text = value
        return decode_text(text.encode('latin-1', 'replace'), charset)
    if '=?' in value:
        out = []
        for data, charset in email.header.decode_header(value):
            out.append(decode_text(data, charset) if isinstance(data, bytes) else data)
        return ''.join(out)
    return value


def attachment_name(raw_part):
    """compat32 で読んだパートから添付ファイル名を取り出す。取れなければ None。"""
    for header, key in (('content-disposition', 'filename'), ('content-type', 'name')):
        raw = next((v for k, v in raw_part.raw_items() if k.lower() == header), None)
        if raw is None:
            continue
        raw = re.sub(r'\r?\n(?=[ \t])', '', str(raw))
        tmp = email.message.Message()
        tmp[header] = fix_8bit(raw)
        for name, value in tmp.get_params(header=header)[1:]:
            if name.lower() == key:
                return _param_text(value).strip() or None
    return None


def safe_name(name):
    name = os.path.basename(name.replace('\\', '/')).strip().strip('\x00')
    return re.sub(r'[<>:"|?*\x00-\x1f]', '_', name) or 'attachment'


class _EmlArchive:
    """1つの .eml をパースして仮想ツリーを作る。mtime が変わるまでキャッシュする。"""

    _cache = {}

    def __init__(self, eml_path):
        self.eml_path = eml_path
        self.mtime = os.path.getmtime(eml_path)
        self.root_files = {}    # 'header.txt', 'message000(utf-8).txt', ...
        self.attach_files = {}  # 添付ファイル名 -> bytes
        self._parse()

    @classmethod
    def get(cls, eml_path):
        eml_path = os.path.abspath(eml_path)
        mtime = os.path.getmtime(eml_path)  # ファイルが無ければここで例外
        cached = cls._cache.get(eml_path)
        if cached is None or cached.mtime != mtime:
            cached = cls(eml_path)
            cls._cache[eml_path] = cached
        return cached

    @staticmethod
    def _unique(used, name):
        if name not in used:
            used.add(name)
            return name
        stem, dot, ext = name.rpartition('.')
        n = 2
        while True:
            candidate = f'{stem}_{n}.{ext}' if dot else f'{name}_{n}'
            if candidate not in used:
                used.add(candidate)
                return candidate
            n += 1

    def _parse(self):
        with open(self.eml_path, 'rb') as f:
            raw = f.read()
        msg = email.message_from_bytes(raw, policy=email.policy.default)

        headers = '\n'.join(f'{k}: {v}' for k, v in msg.items())
        self.root_files['header.txt'] = (headers + '\n').encode('utf-8')

        used_msg, used_attach = set(), set()
        idx = 0
        # 添付名は日本語メーラー特有の崩れ (生の8bit・shift_jis 指定の機種依存文字) を
        # 自前で復号したいので、同じ構造の compat32 解析と並べて歩く
        raw_parts = [p for p in email.message_from_bytes(raw).walk() if not p.is_multipart()]
        parts = [p for p in msg.walk() if not p.is_multipart()]
        for part, raw_part in zip(parts, raw_parts):
            filename = attachment_name(raw_part) or part.get_filename()
            try:
                payload = part.get_payload(decode=True) or b''
            except Exception:
                payload = b''

            if filename:
                # 添付ファイル（本文以外の text/plain 添付もここに来る）
                name = self._unique(used_attach, safe_name(filename))
                self.attach_files[name] = payload
                continue

            content_type = part.get_content_type()
            if content_type in ('text/plain', 'text/html'):
                charset = part.get_content_charset() or 'us-ascii'
                ext = 'html' if content_type == 'text/html' else 'txt'
                name = self._unique(used_msg, f'message{idx:03d}({charset}).{ext}')
                self.root_files[name] = payload
                idx += 1
            else:
                # ファイル名の無い inline パート（画像など）は attach 側へ
                ext = content_type.split('/')[-1] or 'bin'
                name = self._unique(used_attach, f'part{idx:03d}.{ext}')
                self.attach_files[name] = payload
                idx += 1


class EmlPathImpl(ReadOnlyPathImpl):
    """eml://<.emlの絶対パス>#<内部パス> を仮想フォルダとして見せる。"""

    SCHEME = 'eml'
    READ_ONLY_MESSAGE = '.eml の内容を編集するには元のメールソフトを使ってください'

    def parse_uri(self, uri):
        prefix = f'{self.SCHEME}://'
        if not uri.startswith(prefix):
            raise ValueError(f'not an {prefix} URI: {uri!r}')
        eml_path, sep, key = uri[len(prefix):].partition('#')
        if not sep:
            raise ValueError(f'missing # in {uri!r}')
        self._eml_path = eml_path
        return key

    def root_prefix(self):
        return f'{self.SCHEME}://{self._eml_path}#'

    @property
    def parent(self):
        # ルート(key=='')の親は .eml が置かれている実フォルダそのもの。
        # xefm.path.Path は直接 import できないので、at() が返す Path のクラスで包む
        if not self.key:
            return type(self.at(''))(os.path.dirname(self._eml_path))
        return super().parent

    @property
    def name(self):
        # ルート(key=='')の名前は .eml ファイル自身の名前
        # → BACKSPACE(go_parent)がこの名前でカーソルを探すため必須
        if not self.key:
            return os.path.basename(self._eml_path)
        return super().name

    def _archive(self):
        return _EmlArchive.get(self._eml_path)

    def _lookup(self):
        try:
            arc = self._archive()
        except OSError:
            return None
        if self.key == '':
            return b''
        if self.key == 'attach':
            return b'' if arc.attach_files else None
        if self.key in arc.root_files:
            return arc.root_files[self.key]
        if self.key.startswith('attach/'):
            return arc.attach_files.get(self.key[len('attach/'):])
        return None

    def exists(self):
        return self._lookup() is not None

    def is_dir(self):
        if self.key == '':
            return True
        if self.key == 'attach':
            try:
                return bool(self._archive().attach_files)
            except OSError:
                return False
        return False

    def iterdir(self):
        arc = self._archive()
        if self.key == '':
            for name in arc.root_files:
                yield self.child(name)
            if arc.attach_files:
                yield self.child('attach')
        elif self.key == 'attach':
            for name in arc.attach_files:
                yield self.child(name)

    def stat(self):
        arc = self._archive()
        if self.is_dir():
            return UriStatResult(is_dir=True, mtime=arc.mtime)
        data = self._lookup() or b''
        return UriStatResult(size=len(data), mtime=arc.mtime, is_dir=False)

    def open(self, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
        if self.is_dir():
            raise OSError(errno.EISDIR, 'Is a directory', str(self))
        data = self._lookup()
        if data is None:
            raise OSError(errno.ENOENT, 'No such file', str(self))
        if 'b' in mode:
            return io.BytesIO(data)
        return io.StringIO(data.decode(encoding or 'utf-8', errors=errors or 'replace'))


def open_eml(ctx, path):
    """.eml を Enter したら仮想フォルダへ cd する。open_file() の分岐から呼ぶ。"""
    ctx.pane.cd(f'eml://{path.absolute()}#')
    return True


# ---- .msg (Outlook) ---------------------------------------------------------------------

def _text(value):
    return "" if value is None else str(value).strip()


def _body(msg):
    body = msg.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if body:
        return body.replace("\r\n", "\n")
    html = msg.htmlBody
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    if html:
        html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
        return re.sub(r"<[^>]+>", "", html)
    return ""


def _attachment_data(att):
    data = getattr(att, "data", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    export = getattr(data, "exportBytes", None)   # 添付されたメール (入れ子の .msg)
    return export() if export else b""


def format_message(msg, attachments):
    """ヘッダ・添付ファイル名・本文を 1つのテキストにする。attachments は名前のリスト。"""
    lines = [
        f"From   : {_text(msg.sender)}",
        f"To     : {_text(msg.to)}",
        f"Cc     : {_text(msg.cc)}",
    ]
    if _text(msg.bcc):
        lines.append(f"Bcc    : {_text(msg.bcc)}")
    lines += [
        f"Date   : {_text(msg.date)}",
        f"Subject: {_text(msg.subject)}",
    ]
    if attachments:
        lines.append(f"Attach : {len(attachments)}")
        lines += [f"  - {name}" for name in attachments]
    lines += ["", "-" * 72, "", _body(msg)]
    return "\n".join(lines) + "\n"


class _MsgArchive(_EmlArchive):
    _cache = {}

    def _parse(self):
        import extract_msg

        msg = extract_msg.Message(self.eml_path)
        try:
            used, names = set(), []
            for i, att in enumerate(msg.attachments):
                name = (getattr(att, "longFilename", None) or getattr(att, "shortFilename", None)
                        or getattr(att, "name", None) or f"attachment{i:03d}")
                name = self._unique(used, os.path.basename(str(name)))
                self.attach_files[name] = _attachment_data(att)
                names.append(name)
            self.root_files["message.txt"] = format_message(msg, names).encode("utf-8")
        finally:
            msg.close()


class MsgPathImpl(EmlPathImpl):
    """msg://<.msgの絶対パス>#<内部パス>。構造は eml:// と同じ。"""

    SCHEME = "msg"
    READ_ONLY_MESSAGE = ".msg の内容を編集するには Outlook を使ってください"

    def _archive(self):
        return _MsgArchive.get(self._eml_path)


def open_msg(ctx, path):
    """.msg を Enter したら仮想フォルダへ cd する。"""
    ctx.pane.cd(f"msg://{path.absolute()}#")
    return True


EML_SCHEME = {"class": EmlPathImpl, "override": True}
MSG_SCHEME = {"class": MsgPathImpl, "override": True}

