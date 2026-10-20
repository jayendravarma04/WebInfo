import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .agent import run_agent
from .browser import navigator
from .config import get_settings
from .llm import QuotaExhausted, get_gemini

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webinfo")
_here = Path(__file__).resolve()
FRONTEND = next((d for d in (_here.parents[1] / "frontend", _here.parents[2] / "frontend") if d.exists()),
                _here.parents[1] / "frontend")
_run_slots = asyncio.Semaphore(1)   # one question at a time: the browser tab ids (P1, P2…) are shared, and an e2-micro has one tab to spare


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    await navigator.start()
    await get_gemini().resolve_model()
    yield
    await navigator.stop()
    await db.close_db()


app = FastAPI(title="WebInfo Agent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _client_ip(req: Request) -> str:
    # X-Forwarded-For is set by the client unless a proxy you control rewrites it, so trusting it
    # by default would let anyone dodge the per-IP limit and drain the free API quota.
    fwd = req.headers.get("x-forwarded-for", "") if get_settings().trust_proxy else ""
    return (fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?"))[:64]


def _require_admin(token: str | None) -> None:
    want = get_settings().admin_token
    if not want or not token or not hmac.compare_digest(token, want):
        raise HTTPException(404, "Not found")   # don't advertise that the endpoint exists


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@app.get("/api/health")
async def health():
    s = get_settings()
    return {"ok": bool(s.gemini_keys and s.tavily_api_key), "model": get_gemini().model}


@app.get("/api/usage")
async def usage(x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)
    s = get_settings()
    return {"caps": {"gemini_per_key_per_day": s.gemini_daily_cap, "tavily_per_month": s.tavily_monthly_cap},
            "usage": await db.usage_summary()}


@app.get("/api/history")
async def history(limit: int = Query(20, le=100), x_admin_token: str | None = Header(None)):
    _require_admin(x_admin_token)   # other people's questions are private
    return await db.runs_recent(limit)


@app.get("/api/ask")
async def ask(request: Request, q: str = Query(..., min_length=3, max_length=500)):
    """Server-Sent Events stream. See docs/EVENTS.md for the event contract."""
    s = get_settings()
    ip = _client_ip(request)
    if await db.ip_runs_last_hour(ip) >= s.per_ip_hourly_limit:
        raise HTTPException(429, "Hourly question limit reached — protects the free API quota.")

    run_id = uuid.uuid4()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    t0 = time.monotonic()

    async def emit(event: str, data: dict) -> None:
        await queue.put(_sse(event, data))

    async def worker():
        status, answer, cached = "error", None, False
        await db.run_start(run_id, q, ip)
        try:
            async with _run_slots:
                await emit("run", {"run_id": str(run_id), "question": q})
                answer = await run_agent(q, emit)
                cached = bool(answer.get("cached"))
                status = "done"
        except QuotaExhausted as e:
            await emit("error", {"code": "quota", "message": f"Free API quota guard: {e}"})
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("run failed")
            await emit("error", {"code": "agent", "message": str(e)[:300]})
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            await db.run_finish(run_id, answer, status, cached, ms)
            await queue.put(_sse("done", {"run_id": str(run_id), "status": status, "latency_ms": ms}))
            await queue.put(None)

    task = asyncio.create_task(worker())

    async def stream():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield item
                if await request.is_disconnected():
                    task.cancel()
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- frontend (placeholder until the real UI lands) ----
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    async def index():
        return FileResponse(FRONTEND / "index.html")
