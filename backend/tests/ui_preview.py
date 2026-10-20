"""Serve the real app + fake Tavily/Gemini locally for UI previews:  python -m tests.ui_preview"""
import os, re, json, threading, time
os.environ.update(GEMINI_API_KEYS="good-key", TAVILY_API_KEY="tvly-test", ALLOW_PRIVATE_HOSTS="true",
                  GEMINI_MIN_INTERVAL_S="0.1", PER_IP_HOURLY_LIMIT="1000")
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
FAKE = 9100
fake = FastAPI()
def para(n): return "".join(f"<p>Section {i}: Acme builds modular widgets for makers, labs and classrooms, shipped worldwide with a two-year warranty.</p>" for i in range(n))
STYLE = "<style>body{font:18px Georgia;max-width:820px;margin:40px auto;color:#222}h1{font-size:44px}nav{background:#0a3;color:#fff;padding:14px}</style>"
@fake.get("/site/index.html", response_class=HTMLResponse)
def index(): return f"<html><title>Acme Home</title>{STYLE}<body><nav>Acme</nav><h1>Acme Widgets</h1>{para(10)}<a href='/site/pricing.html'>See pricing plans</a></body></html>"
@fake.get("/site/pricing.html", response_class=HTMLResponse)
def pricing(): return f"<html><title>Acme Pricing</title>{STYLE}<body><nav>Acme</nav><h1>Pricing</h1>{para(6)}<p>The Pro plan costs $49 per month and includes 10 seats.</p>{para(4)}</body></html>"
@fake.post("/tavily/search")
async def tavily(req: Request):
    return {"results": [{"title": "Acme", "url": f"http://127.0.0.1:{FAKE}/site/index.html", "content": "Acme widgets home", "score": .9}]}
@fake.get("/gemini/models")
def models(): return {"models": [{"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]}]}
@fake.post("/gemini/models/{model}:generateContent")
async def gen(model: str, req: Request):
    body = await req.json(); prompt = body["contents"][0]["parts"][0]["text"]
    m = re.search(r"\[(P\d):B(\d+)\] [^\n]*\$49", prompt)
    out = ({"found": True, "answer": "The Pro plan is **$49 per month**. It includes 10 seats and is billed monthly.", "confidence": .93,
            "citations": [{"page": m.group(1), "block": int(m.group(2)), "quote": "The Pro plan costs $49 per month"}], "follow": None} if m else
           {"found": False, "answer": "Not on home page", "confidence": .2, "citations": [],
            "follow": {"page": "P1", "url": f"http://127.0.0.1:{FAKE}/site/pricing.html", "why": "pricing page"}})
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(out)}]}}]}
def serve(app, port): uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
if __name__ == "__main__":
    from app import search, llm, main as appmain
    search.TAVILY_URL = f"http://127.0.0.1:{FAKE}/tavily/search"; llm.API = f"http://127.0.0.1:{FAKE}/gemini"
    threading.Thread(target=serve, args=(fake, FAKE), daemon=True).start()
    serve(appmain.app, 8077)
