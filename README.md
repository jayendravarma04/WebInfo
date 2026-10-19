# WebInfo — Intelligent Web Information Retrieval Agent

Ask a question in plain language → the agent **searches** (Tavily), **navigates** real sites in headless Chromium,
**reads** them, **follows a link** if the answer isn't on the first pages, and returns the **specific answer with the exact quote**,
while streaming screenshots + cursor coordinates so the UI can show a live "gaming cursor" moving over each site.

```
question ─► Tavily search (1 credit, cached 24h)
         ─► Chromium visits top 3 sites  ──► screenshots + cursor events ─► UI
         ─► Gemini Flash-Lite extracts answer + verbatim citations (1 call)
         ─► (not found? follow best link, 1 more call)
         ─► revisit source, scroll to quote, highlight ─► answer (cached 12h)
```

**Stack:** FastAPI · Playwright/Chromium · Gemini REST (2-key rotation) · Tavily · Postgres 16 · Docker Compose · GCP e2-micro

## Free-tier guard rails
| Limit | Default | Where |
|---|---|---|
| Gemini calls / key / day | 400 (free Flash-Lite ≈ 500) | `GEMINI_DAILY_CAP` |
| Gemini spacing per key | 4 s | `GEMINI_MIN_INTERVAL_S` |
| Tavily credits / month | 900 (free = 1000) | `TAVILY_MONTHLY_CAP` |
| Questions / IP / hour | 15 | `PER_IP_HOURLY_LIMIT` |
| Gemini calls per question | 1–2 | `MAX_HOPS` |
| Repeat question | 0 API calls (Postgres cache) | `ANSWER_CACHE_HOURS` |

Model: blank `GEMINI_MODEL` auto-selects the newest Flash-Lite via ListModels (free call). A 429 benches a key for 60 s and rotates to the other.
Keys are never stored in the DB — only a 10-char hash fingerprint for the quota ledger.
The browser refuses private/metadata IPs (SSRF guard), and page text is treated as untrusted data in the prompt.

## Deploy on GCP (free tier)
1. Open **Cloud Shell** in the console, then:
   ```bash
   git clone https://github.com/jayendravarma04/webinfo && cd webinfo   # private repo: use a GitHub token when prompted
   gcloud config set project <YOUR_PROJECT_ID>
   bash deploy/create-vm.sh
   ```
2. Wait ~3 min, then SSH and install:
   ```bash
   gcloud compute ssh webinfo-vm --zone us-central1-a
   sudo git clone https://github.com/jayendravarma04/webinfo /opt/webinfo
   cd /opt/webinfo && sudo bash deploy/install.sh     # prompts for keys (hidden input)
   ```
3. Open `http://<EXTERNAL_IP>/`. Updates: `sudo bash deploy/update.sh`.

Cost: e2-micro + 30 GB pd-standard in us-central1 are Always-Free. Postgres runs in Docker on the same VM (Cloud SQL has no free tier).
The VM's external IPv4 may bill a few $/month — covered by trial credit. Stop the VM when not demoing: `gcloud compute instances stop webinfo-vm --zone us-central1-a`.

## Local dev / test
```bash
cd backend && pip install -r requirements.txt && playwright install chromium
DATABASE_URL=postgresql://... python -m tests.e2e_mock     # offline E2E with fake Tavily/Gemini
```
UI contract: [`docs/EVENTS.md`](docs/EVENTS.md). `frontend/index.html` is a temporary harness — replaced by the real design.
