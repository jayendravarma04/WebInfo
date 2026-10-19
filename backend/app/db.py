"""Postgres: response cache, API quota ledger, and run history."""
import hashlib
import json
from datetime import date

import asyncpg

from .config import get_settings

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS cache_expires_idx ON cache (expires_at);

-- One row per provider/key/period. period = 'YYYY-MM-DD' (Gemini) or 'YYYY-MM' (Tavily).
CREATE TABLE IF NOT EXISTS usage (
    provider TEXT NOT NULL,
    key_id   TEXT NOT NULL,
    period   TEXT NOT NULL,
    count    INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, key_id, period)
);

CREATE TABLE IF NOT EXISTS runs (
    id          UUID PRIMARY KEY,
    question    TEXT NOT NULL,
    answer      JSONB,
    status      TEXT NOT NULL DEFAULT 'running',
    client_ip   TEXT,
    cached      BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms  INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_ip_time_idx ON runs (client_ip, created_at);
"""


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(get_settings().database_url, min_size=1, max_size=4)
    async with _pool.acquire() as c:
        await c.execute(SCHEMA)
        await c.execute("DELETE FROM cache WHERE expires_at < now()")


async def close_db() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB not initialised"
    return _pool


def make_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


# ---------- cache ----------
async def cache_get(key: str):
    row = await pool().fetchrow(
        "SELECT value FROM cache WHERE key=$1 AND expires_at > now()", key
    )
    return json.loads(row["value"]) if row else None


async def cache_set(key: str, value, hours: float) -> None:
    await pool().execute(
        """INSERT INTO cache (key, value, expires_at)
           VALUES ($1, $2::jsonb, now() + make_interval(secs => $3))
           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, expires_at=EXCLUDED.expires_at""",
        key, json.dumps(value), hours * 3600,
    )


# ---------- quota ledger ----------
def key_id(secret: str) -> str:
    """Never store raw API keys — only a short fingerprint."""
    return hashlib.sha256(secret.encode()).hexdigest()[:10]


async def try_consume(provider: str, kid: str, period: str, cap: int, amount: int = 1) -> bool:
    """Atomically add `amount` to usage if it stays within `cap`. Returns False if over budget."""
    row = await pool().fetchrow(
        """INSERT INTO usage (provider, key_id, period, count) VALUES ($1,$2,$3,$4)
           ON CONFLICT (provider, key_id, period)
           DO UPDATE SET count = usage.count + EXCLUDED.count
           WHERE usage.count + EXCLUDED.count <= $5
           RETURNING count""",
        provider, kid, period, amount, cap,
    )
    if row is None:
        return False
    return row["count"] <= cap


async def usage_summary() -> list[dict]:
    today, month = date.today().isoformat(), date.today().isoformat()[:7]
    rows = await pool().fetch(
        "SELECT provider, key_id, period, count FROM usage WHERE period IN ($1,$2) ORDER BY provider",
        today, month,
    )
    return [dict(r) for r in rows]


# ---------- runs ----------
async def run_start(run_id, question: str, ip: str) -> None:
    await pool().execute(
        "INSERT INTO runs (id, question, client_ip) VALUES ($1,$2,$3)", run_id, question, ip
    )


async def run_finish(run_id, answer: dict | None, status: str, cached: bool, latency_ms: int) -> None:
    await pool().execute(
        "UPDATE runs SET answer=$2::jsonb, status=$3, cached=$4, latency_ms=$5 WHERE id=$1",
        run_id, json.dumps(answer) if answer else None, status, cached, latency_ms,
    )


async def runs_recent(limit: int = 20) -> list[dict]:
    rows = await pool().fetch(
        """SELECT id, question, answer->>'answer' AS answer, status, cached, latency_ms, created_at
           FROM runs ORDER BY created_at DESC LIMIT $1""",
        limit,
    )
    return [{**dict(r), "id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in rows]


async def ip_runs_last_hour(ip: str) -> int:
    return await pool().fetchval(
        "SELECT count(*) FROM runs WHERE client_ip=$1 AND cached=FALSE AND created_at > now() - interval '1 hour'",
        ip,
    )
