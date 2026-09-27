"""文件名安全校验与服务端存储名生成。

显示文件名允许中文，但必须阻止路径穿越、绝对路径、盘符、UNC、
控制字符、保留设备名、路径分隔符伪装与双向文本伪装。
磁盘路径永远使用服务端生成的存储名，绝不使用用户文件名。
"""

from __future__ import annotations

import re
import unicodedata
import uuid

from app.core.errors import DOCUMENT_UNSAFE_NAME, ApiError

MAX_DISPLAY_NAME_LENGTH = 200

# 与 ``/`` ``\`` 形近的路径分隔符伪装
SEPARATOR_LOOKALIKES = frozenset(
    {
        "\u2044",  # ⁄
        "\u2215",  # ∕
        "\u2216",  # ∖
        "\u29f5",  # ⧵
        "\u29f8",  # ⧸
        "\ufe68",  # ﹨
        "\uff0f",  # ／
        "\uff3c",  # ＼
    }
)

# 双向/格式控制字符，常用于文件名伪装
BIDI_CONTROLS = frozenset(
    [chr(code) for code in range(0x202A, 0x202F)]
    + [chr(code) for code in range(0x2066, 0x206A)]
    + ["\u200e", "\u200f", "\u206a", "\u206b", "\u206c", "\u206d", "\u206e", "\u206f", "\ufeff"]
)

RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_EXTENSION_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")


def _reject(reason: str) -> ApiError:
    return ApiError(DOCUMENT_UNSAFE_NAME, details={"reason": reason})


def validate_display_name(raw: str | None) -> str:
    """校验并返回可安全保存的显示文件名；不安全时抛出 ``DOCUMENT_UNSAFE_NAME``。"""
    name = unicodedata.normalize("NFC", raw or "")

    if not name or name in {".", ".."}:
        raise _reject("empty")
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise _reject("too_long")
    if name != name.strip():
        raise _reject("surrounding_whitespace")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        raise _reject("control_character")
    if "/" in name or "\\" in name:
        raise _reject("path_separator")
    if any(char in SEPARATOR_LOOKALIKES for char in name):
        raise _reject("separator_lookalike")
    if any(char in BIDI_CONTROLS for char in name):
        raise _reject("bidi_control")
    if ".." in name:
        raise _reject("parent_reference")
    if _DRIVE_RE.match(name):
        raise _reject("drive_letter")
    if name.endswith(" ") or name.endswith("."):
        raise _reject("trailing_space_or_dot")
    if name.split(".")[0].casefold() in RESERVED_STEMS:
        raise _reject("reserved_device_name")
    return name


def split_extension(name: str) -> tuple[str, str]:
    """返回 (主干, 扩展名小写)；无扩展名时扩展名为空串。"""
    if "." not in name:
        return name, ""
    stem, _, extension = name.rpartition(".")
    if not _EXTENSION_RE.match(extension):
        return name, ""
    return stem, f".{extension.lower()}"


def build_storage_name(sha256: str, file_type: str) -> str:
    """服务端生成的存储名：校验值前缀 + 随机后缀，不含用户输入。"""
    return f"{sha256[:32]}-{uuid.uuid4().hex[:12]}.{file_type}"
