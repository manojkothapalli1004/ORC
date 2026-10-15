"""Shared atomic file-write helper.

The process umask is read exactly once, at import time. Reading it requires
setting it, so we set it to 0 and immediately restore it. This happens during
import, before any worker threads use this module. No other code in the
backend may call ``os.umask``, so the process umask is never changed while
other threads are creating files.

File modes:

* New files get ``0o666 & ~umask``, the same as a plain ``open(path, "w")``.
* Existing files keep their permission bits (``& 0o777``). Setuid, setgid and
  sticky bits are not carried over.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

_UMASK = os.umask(0)
os.umask(_UMASK)


def _target_mode(path: Path) -> int:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return 0o666 & ~_UMASK
    return stat.S_IMODE(st.st_mode) & 0o777


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a unique temp file + os.replace."""
    path = Path(path)
    tmp = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_name = tmp.name
    try:
        with tmp:
            tmp.write(text)
            tmp.flush()
        os.chmod(tmp_name, _target_mode(path))
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
