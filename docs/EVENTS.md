# SSE event contract (for the UI)

`GET /api/ask?q=<question>` → `text/event-stream`. Use `EventSource` and listen per event name.
All coordinates are **screenshot pixels** (`width`×`height`, default 1280×800 viewport). Scale them to your stage:
`left = x / width * 100%`, `top = y / height * 100%`.

| event | data | UI meaning |
|---|---|---|
| `run` | `{run_id, question}` | stream started |
| `status` | `{stage, message}` — stage ∈ `cache·search·browse·read·follow·evidence` | step label / progress |
| `search_results` | `{query, results:[{title,url,snippet,score}]}` | show the sites found |
| `navigate` | `{page_id, url, reason?}` | address bar / "opening…" (`reason:"evidence"` = revisiting the source) |
| `screenshot` | `{page_id, url, image (data:image/jpeg;base64…), width, height, scroll_y}` | swap the stage image |
| `cursor` | `{page_id, x, y, action, label?}` — action ∈ `hover·scroll·move·select` | animate the gaming cursor to (x,y) |
| `page_read` | `{page_id, url, title, blocks, links}` | page done |
| `page_error` | `{page_id, url, message}` | page skipped |
| `highlight` | `{page_id, x, y, w, h, quote}` | box the exact evidence on the current screenshot |
| `answer` | `{answer (markdown), found, confidence, citations:[{page_id,url,title,quote}], sources:[…], latency_ms, cached}` | final answer |
| `error` | `{code: "quota"\|"agent", message}` | show error |
| `done` | `{run_id, status, latency_ms}` | close the stream |

Order for a typical run:
`run → status(search) → search_results → [navigate → screenshot → cursor×N → (cursor scroll → screenshot → cursor×N) → page_read]×pages → status(read) → (status(follow) → navigate …)? → status(evidence) → navigate → screenshot → cursor move → cursor select → highlight → answer → done`

Events arrive in bursts; queue them and replay with ~300–450 ms per `cursor` so motion is visible (see `frontend/index.html`).

Request headers for `/api/ask`: `X-Gemini-Key` and `X-Tavily-Key`, the visitor's own keys (required while `BYOK=true`, the default). They are used for that run only and never stored or logged. A missing key returns `401`; a rejected key ends the stream with `error` `{"code": "key"}`.

Other endpoints: `GET /api/health`. Admin only (send `X-Admin-Token`; switched off while `ADMIN_TOKEN` is blank): `GET /api/usage` (quota ledger), `GET /api/history?limit=20`.
HTTP 429 from `/api/ask` = per-IP hourly limit (protects free quotas).
