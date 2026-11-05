"""Read-only virtual folders for .eml / .msg files (no user actions)."""
from .logic import EmlPathImpl, MsgPathImpl, open_eml, open_msg
from .logic import _EmlArchive  # sqlite-archive builds on it
