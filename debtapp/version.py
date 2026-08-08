"""The commit this process was built from.

Streamlit Community Cloud offers no way to see which commit a running app is
serving, so a push that silently fails to redeploy looks exactly like one that
worked — the only way to tell them apart is to go hunting for some behaviour you
changed. Putting the sha on screen makes that a glance instead.

Resolved once per process and cached: a running app cannot change commit under
its own feet, and shelling out to git on every rerun would be silly.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

UNKNOWN = "unknown"


@lru_cache(maxsize=1)
def build_id() -> str:
    """Short commit sha, or ``"unknown"`` if there is nothing to ask.

    ``DEBTMANAGER_BUILD`` wins when set, for hosts that build from an export
    rather than a checkout and inject the sha as an environment variable. The
    git call is the fallback, and every way it can fail — no git binary, no
    ``.git`` directory, a repository so broken that rev-parse exits non-zero —
    lands on ``"unknown"`` rather than taking the app down. A version badge is
    never worth a crash.
    """
    env = os.environ.get("DEBTMANAGER_BUILD", "").strip()
    if env:
        return env

    try:
        done = subprocess.run(
            ["git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    return done.stdout.strip() or UNKNOWN
