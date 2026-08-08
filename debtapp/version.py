"""The commit this process was built from.

Streamlit Community Cloud offers no way to see which commit a running app is
serving, so a push that silently fails to redeploy looks exactly like one that
worked — the only way to tell them apart is to go hunting for some behaviour you
changed. Putting the sha on screen makes that a glance instead.

Read straight off ``.git`` rather than by shelling out to ``git``: an earlier
cut of this used ``subprocess.run`` and took the deployed app down with a
``ForkImportError``, because forking a child out from under a hosted Streamlit
runtime is not something the sandbox will sit still for. Parsing two small text
files needs no subprocess, no fork, and no git binary on the host.

Resolved once per process and cached — a running app cannot change commit under
its own feet.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

UNKNOWN = "unknown"
_SHORT = 7


def _git_dir(root: Path) -> Path | None:
    """``.git`` is a directory in a clone, but a file pointing elsewhere in a
    worktree — which is exactly how this repo is checked out locally."""
    dot = root / ".git"
    if dot.is_dir():
        return dot
    if dot.is_file():
        pointer = dot.read_text(encoding="utf-8").strip()
        if pointer.startswith("gitdir:"):
            target = Path(pointer.split(":", 1)[1].strip())
            resolved = target if target.is_absolute() else (root / target)
            return resolved if resolved.is_dir() else None
    return None


def _ref_homes(git: Path) -> list[Path]:
    """Where a ref might live: this git dir, and the shared one behind it.

    A linked worktree keeps its own HEAD but not its own branches — those stay
    in the common directory that ``commondir`` points at. Checkouts made by
    Conductor are worktrees, so skipping this leaves every local run reporting
    "unknown".
    """
    homes = [git]
    common = git / "commondir"
    if common.is_file():
        target = Path(common.read_text(encoding="utf-8").strip())
        resolved = target if target.is_absolute() else (git / target)
        if resolved.is_dir():
            homes.append(resolved.resolve())
    return homes


def _resolve(git: Path) -> str:
    """HEAD is either a raw sha (detached) or a ref to chase down."""
    head = (git / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head

    ref = head.split(":", 1)[1].strip()
    for home in _ref_homes(git):
        loose = home / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()

        # Refs get packed away as a repository ages; the loose file is then gone.
        packed = home / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith(("#", "^")):
                    continue
                sha, _, name = line.partition(" ")
                if name.strip() == ref:
                    return sha
    return ""


@lru_cache(maxsize=1)
def build_id() -> str:
    """Short commit sha, or ``"unknown"`` if there is nothing to read.

    ``DEBTMANAGER_BUILD`` wins when set, for hosts that deploy from an export
    rather than a checkout and inject the sha as an environment variable.

    The catch-all is deliberate. This is a diagnostic badge in the corner of the
    screen; there is no failure here worth taking the whole app down for, and
    the last time this module got that wrong it did exactly that.
    """
    env = os.environ.get("DEBTMANAGER_BUILD", "").strip()
    if env:
        return env

    try:
        git = _git_dir(_ROOT)
        sha = _resolve(git) if git else ""
    except (OSError, ValueError, UnicodeDecodeError):
        return UNKNOWN
    return sha[:_SHORT] if sha else UNKNOWN
