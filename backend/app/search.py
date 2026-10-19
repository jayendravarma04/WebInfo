"""Tavily web search with Postgres caching and a monthly credit budget."""
from datetime import date

import httpx

from . import db
from .config import get_settings

TAVILY_URL = "https://api.tavily.com/search"


class SearchUnavailable(RuntimeError):
    pass


async def web_search(query: str) -> list[dict]:
    """Returns [{title, url, snippet, score}]. Uses basic depth = 1 credit per uncached call."""
    s = get_settings()
    ckey = db.make_key("tavily", query.lower().strip(), str(s.tavily_max_results))
    if (hit := await db.cache_get(ckey)) is not None:
        return hit
    if not s.tavily_api_key:
        raise SearchUnavailable("TAVILY_API_KEY not configured")
    month = date.today().isoformat()[:7]
    if not await db.try_consume("tavily", db.key_id(s.tavily_api_key), month, s.tavily_monthly_cap):
        raise SearchUnavailable("Tavily monthly safety cap reached")

    async with httpx.AsyncClient(timeout=25) as http:
        r = await http.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {s.tavily_api_key}"},
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": s.tavily_max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
    if r.status_code in (401, 403, 429, 432, 433):
        raise SearchUnavailable(f"Tavily {r.status_code}: {r.text[:200]}")
    r.raise_for_status()
    results = [
        {"title": x.get("title", ""), "url": x["url"], "snippet": x.get("content", "")[:400],
         "score": x.get("score", 0)}
        for x in r.json().get("results", []) if x.get("url")
    ]
    await db.cache_set(ckey, results, s.search_cache_hours)
    return results
