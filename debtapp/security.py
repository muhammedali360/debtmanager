"""Password policy and recovery codes — pure logic, no storage.

Kept separate from :mod:`debtapp.db` so the rules can be tested without a
database and reused if storage ever moves to Postgres.
"""

from __future__ import annotations

import re
import secrets
import unicodedata

MIN_LENGTH = 10
MAX_BYTES = 72  # bcrypt silently truncates beyond this — reject instead

# The passwords that actually show up in credential-stuffing lists. A short,
# high-signal blocklist beats a long one: attackers try these first.
COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "12345678", "123456789",
    "1234567890", "qwertyuiop", "qwerty123", "letmein", "welcome1", "iloveyou",
    "admin123", "abc12345", "monkey123", "football", "baseball", "sunshine",
    "princess", "trustno1", "dragon123", "superman", "starwars", "whatever",
    "changeme", "secret123", "master123", "shadow123", "michael1", "jennifer",
    "computer", "hunter123", "freedom1", "batman123", "internet", "samsung1",
}

_SEQUENCES = ("abcdefghijklmnopqrstuvwxyz", "01234567890", "qwertyuiop", "asdfghjkl")


def normalize(password: str) -> str:
    """NFKC-normalize so visually identical passwords hash identically."""
    return unicodedata.normalize("NFKC", password or "")


def _has_long_run(pw: str, n: int = 4) -> bool:
    """`aaaa` or `1111` — a run of the same character."""
    run = 1
    for a, b in zip(pw, pw[1:]):
        run = run + 1 if a == b else 1
        if run >= n:
            return True
    return False


def _has_sequence(pw: str, n: int = 5) -> bool:
    """`abcde`, `12345`, `qwert` — forwards or backwards."""
    low = pw.lower()
    for seq in _SEQUENCES:
        for i in range(len(seq) - n + 1):
            chunk = seq[i:i + n]
            if chunk in low or chunk[::-1] in low:
                return True
    return False


def password_problems(password: str, email: str = "") -> list[str]:
    """Every reason this password is unacceptable. Empty list means it passes."""
    pw = normalize(password)
    problems: list[str] = []

    if len(pw) < MIN_LENGTH:
        problems.append(f"Use at least {MIN_LENGTH} characters (longer beats complicated).")
    if len(pw.encode("utf-8")) > MAX_BYTES:
        problems.append(f"Keep it under {MAX_BYTES} bytes.")

    stripped = re.sub(r"[^a-z0-9]", "", pw.lower())
    if pw.lower() in COMMON_PASSWORDS or stripped in COMMON_PASSWORDS:
        problems.append("This is one of the most commonly used passwords in the world.")

    local = (email or "").split("@")[0].lower()
    if local and len(local) >= 3 and local in pw.lower():
        problems.append("Don't put your email address in your password.")

    if _has_long_run(pw):
        problems.append("Avoid runs of the same character like 'aaaa'.")
    if _has_sequence(pw):
        problems.append("Avoid keyboard or alphabet runs like '12345' or 'qwert'.")

    if len(set(pw)) < 5 and len(pw) >= MIN_LENGTH:
        problems.append("Use a wider variety of characters.")

    return problems


def password_strength(password: str) -> tuple[int, str]:
    """A 0–4 score and a label, for the signup meter.

    Length-dominant on purpose: a long passphrase genuinely is stronger than a
    short string with a symbol bolted on, and scoring it that way nudges people
    toward the better habit.
    """
    pw = normalize(password)
    if not pw:
        return 0, "Empty"
    if password_problems(pw):
        # Still show progress toward the bar rather than a flat zero.
        return (0, "Too weak") if len(pw) < MIN_LENGTH else (1, "Weak")

    classes = sum(bool(re.search(p, pw)) for p in
                  (r"[a-z]", r"[A-Z]", r"\d", r"[^a-zA-Z0-9]"))
    score = 2
    if len(pw) >= 14:
        score += 1
    if len(pw) >= 20 or (len(pw) >= 16 and classes >= 3):
        score += 1
    return min(score, 4), {2: "Okay", 3: "Strong", 4: "Very strong"}[min(score, 4)]


# ------------------------------------------------------------- recovery codes

RECOVERY_CODE_COUNT = 8
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1 — transcribable by hand


def generate_recovery_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def generate_recovery_codes(n: int = RECOVERY_CODE_COUNT) -> list[str]:
    return [generate_recovery_code() for _ in range(n)]


def canonical_code(code: str) -> str:
    """Accept the code however the user typed it — case, spaces, dashes."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())
