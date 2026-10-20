"""Bring-your-own-keys: the visitor's Gemini + Tavily keys for the current run only.

Keys arrive as request headers, live in a ContextVar for the length of one run, and are
never written to the database or logs (the quota ledger stores a 10-char hash fingerprint).
"""
import re
from contextvars import ContextVar

_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{16,200}$")
_run_keys: ContextVar[dict | None] = ContextVar("run_keys", default=None)


def valid_key(k: str | None) -> bool:
    return bool(k) and bool(_KEY_RE.match(k))


def set_run_keys(gemini: str, tavily: str) -> None:
    _run_keys.set({"gemini": gemini, "tavily": tavily})


def run_keys() -> dict | None:
    return _run_keys.get()
