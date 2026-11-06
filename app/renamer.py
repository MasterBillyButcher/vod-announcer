"""BS naming convention - HARD RULES, enforced with validation.

Final name:  BS_<YYYY-MM-DD>_<Title>
Multi-part:  BS_<YYYY-MM-DD>_<Title>_P01, _P02, ...

* date is the recording date as shown on the source page (YYYY-MM-DD)
* title: ASCII letters/digits only, spaces -> underscores, no illegal
  filesystem characters, never empty
* the pipeline order download -> verify -> date/title -> rename -> validate
  -> upload is enforced in main.py; uploading an un-renamed file is
  impossible because the upload step takes the validated name as input
"""

from __future__ import annotations

import os
import re
import unicodedata

NAME_RE = re.compile(r"^BS_\d{4}-\d{2}-\d{2}_[A-Za-z0-9_]+(_P\d{2,})?$")
_BAD_FS = re.compile(r'[\\/:*?"<>|]+')


def sanitize_title(title: str) -> str:
    """`Timepass Time 👁️` -> `Timepass_Time`"""
    # fold accents/emoji away, keep ascii
    text = unicodedata.normalize("NFKD", title or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _BAD_FS.sub("", text)
    text = re.sub(r"\s+", "_", text.strip())
    text = re.sub(r"_+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_]", "", text)
    text = text.strip("_")
    return text or "Recording"


def part_suffix(part_number: int | None) -> str:
    if not part_number:
        return ""
    return f"_P{part_number:02d}"


def build_name(rec_date: str, title: str, part_number: int | None = None) -> str:
    """Build the final BS_ name; raises ValueError on bad input."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", rec_date or ""):
        raise ValueError(f"bad recording date: {rec_date!r}")
    clean = sanitize_title(title)
    name = f"BS_{rec_date}_{clean}{part_suffix(part_number)}"
    if not validate(name):
        raise ValueError(f"generated name failed validation: {name!r}")
    return name


def validate(name: str) -> bool:
    return bool(NAME_RE.match(name)) and len(name) <= 200


def rename_file(src_path: str, new_name: str) -> str:
    """Rename inside the same directory; refuses to overwrite."""
    if not validate(new_name):
        raise ValueError(f"refusing to rename to invalid name: {new_name!r}")
    directory = os.path.dirname(os.path.abspath(src_path))
    ext = os.path.splitext(src_path)[1].lower() or ".mp4"
    dst = os.path.join(directory, new_name + ext)
    if os.path.abspath(dst) == os.path.abspath(src_path):
        return dst
    if os.path.exists(dst):
        raise FileExistsError(f"target already exists: {dst}")
    os.rename(src_path, dst)
    return dst
