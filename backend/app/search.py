"""Tavily web search with Postgres caching and a monthly credit budget."""
from datetime import date

import httpx

from . import db
from .config import get_settings
from .keys import run_keys

TAVILY_URL = "https://api.tavily.com/search"


class SearchUnavailable(RuntimeError):
    pass


async def web_search(query: str) -> list[dict]:
    """Returns [{title, url, snippet, score}]. Uses basic depth = 1 credit per uncached call."""
    s = get_settings()
    ckey = db.make_key("tavily-raw", query.lower().strip(), str(s.tavily_max_results))
    if (hit := await db.cache_get(ckey)) is not None:
        return hit
    rk = run_keys()
    key = rk["tavily"] if rk else ("" if s.byok else s.tavily_api_key)   # never fall back to the server key in BYOK mode
    if not key:
        raise SearchUnavailable("No Tavily API key")
    month = date.today().isoformat()[:7]
    if not await db.try_consume("tavily", db.key_id(key), month, s.tavily_monthly_cap):
        raise SearchUnavailable("Tavily monthly safety cap reached")

    async with httpx.AsyncClient(timeout=25) as http:
        r = await http.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": s.tavily_max_results,
                "include_answer": False,
                "include_raw_content": True,   # page text comes with the search: same 1 credit
            },
        )
    if r.status_code in (401, 403):
        raise SearchUnavailable("The Tavily API key was rejected. Check it at app.tavily.com.")
    if r.status_code in (429, 432, 433):
        raise SearchUnavailable(f"Tavily {r.status_code}: {r.text[:200]}")
    r.raise_for_status()
    results = [
        {"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")[:400],
         "score": x.get("score", 0), "raw": (x.get("raw_content") or "")[:14000]}
        for x in r.json().get("results", []) if x.get("url")
    ]
    await db.cache_set(ckey, results, s.search_cache_hours)
    return results
