"""Offline end-to-end test: real browser + real Postgres + real HTTP clients,
with Tavily and Gemini replaced by a local fake server. Run from backend/:
    DATABASE_URL=... python -m tests.e2e_mock
"""
import asyncio, json, os, re, sys, threading, time

import httpx, uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

FAKE = 9100
os.environ.update(GEMINI_API_KEYS="bad-key,good-key", TAVILY_API_KEY="tvly-test",
                  ALLOW_PRIVATE_HOSTS="true", GEMINI_MIN_INTERVAL_S="0.1", ADMIN_TOKEN="test-admin")

fake = FastAPI()
ADM = {"X-Admin-Token": "test-admin"}
calls = {"gemini": 0, "tavily": 0}
def lorem(n): return "".join(f"<p>Paragraph {i}: " + "WebInfo test filler content to exercise scrolling and extraction. " * 6 + "</p>" for i in range(n))

@fake.get("/site/index.html", response_class=HTMLResponse)
def index():
    return f"<html><title>Acme Home</title><body><h1>Acme Widgets</h1>{lorem(8)}<a href='/site/pricing.html'>See pricing plans</a></body></html>"

@fake.get("/site/pricing.html", response_class=HTMLResponse)
def pricing():
    return f"<html><title>Acme Pricing</title><body><h1>Pricing</h1>{lorem(6)}<p>The Pro plan costs $49 per month and includes 10 seats.</p>{lorem(3)}</body></html>"

@fake.post("/tavily/search")
async def tavily(req: Request):
    calls["tavily"] += 1
    if "quick" in (await req.json())["query"]:   # fast path: page text arrives with the search
        return {"results": [{"title": "Acme Pricing", "url": f"http://127.0.0.1:{FAKE}/site/pricing.html", "content": "pricing", "score": .9,
                             "raw_content": "# Pricing\n" + "Acme widgets text. " * 30 + "\nThe Pro plan costs $49 per month and includes 10 seats.\n" + "More text. " * 30}]}
    assert req.headers["authorization"] == "Bearer tvly-test"
    return {"results": [{"title": "Acme", "url": f"http://127.0.0.1:{FAKE}/site/index.html", "content": "Acme widgets", "score": .9},
                        {"title": "doc", "url": f"http://127.0.0.1:{FAKE}/site/x.pdf", "content": "", "score": .5}]}

@fake.get("/gemini/models")
def models():
    return {"models": [{"name": n, "supportedGenerationMethods": ["generateContent"]} for n in
            ("models/gemini-2.5-flash", "models/gemini-3.5-flash-lite", "models/gemini-2.5-flash-lite", "models/gemini-3-flash-preview")]}

@fake.post("/gemini/models/{model}:generateContent")
async def gen(model: str, req: Request):
    if req.headers["x-goog-api-key"] == "bad-key":
        return JSONResponse({"error": "quota"}, status_code=429)
    calls["gemini"] += 1
    body = await req.json()
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    prompt = body["contents"][0]["parts"][0]["text"]
    m = re.search(r"\[([PR]\d):B(\d+)\] [^\n]*\$49", prompt)
    if not m:
        out = {"found": False, "answer": "Not on home page", "confidence": .2, "citations": [],
               "follow": {"page": "P1", "url": f"http://127.0.0.1:{FAKE}/site/pricing.html", "why": "pricing page"}}
    else:
        out = {"found": True, "answer": "The **Pro plan is $49/month** (10 seats).", "confidence": .93,
               "citations": [{"page": m.group(1), "block": int(m.group(2)), "quote": "The Pro plan costs $49 per month"}], "follow": None}
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}], "_model": model}

def serve(app, port):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")

async def main():
    from app import search, llm, main as appmain
    search.TAVILY_URL = f"http://127.0.0.1:{FAKE}/tavily/search"
    llm.API = f"http://127.0.0.1:{FAKE}/gemini"
    threading.Thread(target=serve, args=(fake, FAKE), daemon=True).start()
    threading.Thread(target=serve, args=(appmain.app, 8077), daemon=True).start()
    async with httpx.AsyncClient(timeout=90) as c:
        for _ in range(60):
            try:
                if (await c.get("http://127.0.0.1:8077/api/health")).status_code == 200: break
            except Exception: pass
            await asyncio.sleep(0.5)
        print("health:", (await c.get("http://127.0.0.1:8077/api/health")).json())
        for attempt in (1, 2):
            t = time.time(); events = []
            async with c.stream("GET", "http://127.0.0.1:8077/api/ask", params={"q": "How much is Acme Pro plan?"}) as r:
                ev = None
                async for line in r.aiter_lines():
                    if line.startswith("event: "): ev = line[7:]
                    elif line.startswith("data: "): events.append((ev, json.loads(line[6:])))
            kinds = [e for e, _ in events]
            print(f"\nrun {attempt}: {time.time()-t:.1f}s  events:", {k: kinds.count(k) for k in dict.fromkeys(kinds)})
            ans = next((d for e, d in events if e == "answer"), None)
            err = next((d for e, d in events if e == "error"), None)
            print("answer:", ans and {k: ans[k] for k in ("answer", "found", "cached", "citations")}, "error:", err)
            if attempt == 1:
                assert ans and ans["found"] and "$49" in ans["answer"], "no answer"
                assert "highlight" in kinds and "cursor" in kinds and "screenshot" in kinds
                assert any(d.get("url", "").endswith("pricing.html") for e, d in events if e == "navigate"), "did not follow link"
                hl = next(d for e, d in events if e == "highlight"); print("highlight box:", {k: hl[k] for k in "xywh"})
            else:
                assert ans["cached"], "second run should be cached"
        t = time.time(); evs = []
        async with c.stream("GET", "http://127.0.0.1:8077/api/ask", params={"q": "quick: price of the Acme Pro plan?"}) as r:
            ev = None
            async for line in r.aiter_lines():
                if line.startswith("event: "): ev = line[7:]
                elif line.startswith("data: "): evs.append((ev, json.loads(line[6:])))
        ans = next(d for e, d in evs if e == "answer"); kinds = [e for e, _ in evs]
        print(f"\nfast path: {time.time()-t:.1f}s found={ans['found']} cite={ans['citations'][0]['page_id']} highlight={'highlight' in kinds}")
        assert ans["found"] and "highlight" in kinds
        print("\nupstream calls:", calls)
        print("usage:", (await c.get("http://127.0.0.1:8077/api/usage", headers=ADM)).json())
        print("history:", [(h["question"], h["status"], h["cached"]) for h in (await c.get("http://127.0.0.1:8077/api/history", headers=ADM)).json()])
    assert calls == {"gemini": 3, "tavily": 2}, calls
    print("\nE2E OK")

asyncio.run(main())
