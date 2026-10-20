"""Gemini client: REST, multi-key rotation, per-key pacing, daily caps, JSON output."""
import asyncio
import json
import logging
import re
import time
from datetime import date

import httpx

from . import db
from .config import get_settings
from .keys import run_keys

log = logging.getLogger("webinfo.llm")
API = "https://generativelanguage.googleapis.com/v1beta"

# Preference order when GEMINI_MODEL is blank. Flash-Lite first: much higher free daily quota.
MODEL_PREFERENCE = [r"flash-lite(?!.*(preview|exp|tts|live|image))", r"flash(?!.*(preview|exp|tts|live|image|lite))"]


def _think_cfg(model: str) -> dict:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model)
    return {"thinkingLevel": "minimal"} if m and float(m.group(1)) >= 3 else {"thinkingBudget": 0}


class QuotaExhausted(RuntimeError):
    pass


class KeyRejected(RuntimeError):
    pass


_shared = {"model": ""}   # the resolved model name is the same for every key


class GeminiClient:
    def __init__(self, keys: list[str] | None = None) -> None:
        s = get_settings()
        self.keys = s.gemini_keys if keys is None else keys
        self.model = s.gemini_model or _shared["model"]
        self._last_call = {k: 0.0 for k in self.keys}
        self._locks = {k: asyncio.Lock() for k in self.keys}
        self._cursor = 0
        self._cooldown_until = {k: 0.0 for k in self.keys}
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        self._think_off: bool | None = None

    async def resolve_model(self) -> str:
        """ListModels does not consume generateContent quota."""
        if self.model or not self.keys:
            return self.model
        try:
            r = await self.http.get(f"{API}/models", params={"pageSize": 200},
                                    headers={"x-goog-api-key": self.keys[0]})
            r.raise_for_status()
            names = [m["name"].removeprefix("models/") for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            for pattern in MODEL_PREFERENCE:
                cands = [n for n in names if re.search(pattern, n)]
                if cands:
                    # highest version number wins, e.g. gemini-3.5-flash-lite > gemini-2.5-flash-lite
                    cands.sort(key=lambda n: [float(x) for x in re.findall(r"\d+(?:\.\d+)?", n)[:1]] or [0],
                               reverse=True)
                    self.model = cands[0]
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("ListModels failed: %s", e)
        if self.model:
            _shared["model"] = self.model   # only a real ListModels result is shared
        self.model = self.model or "gemini-2.5-flash-lite"
        log.info("Using Gemini model: %s", self.model)
        return self.model

    async def _acquire_key(self) -> str:
        s = get_settings()
        today = date.today().isoformat()
        now = time.monotonic()
        order = [self.keys[(self._cursor + i) % len(self.keys)] for i in range(len(self.keys))]
        order.sort(key=lambda k: self._cooldown_until[k] > now)   # healthy keys first
        for i, k in enumerate(order):
            if self._cooldown_until[k] > now and i < len(order) - 1:
                continue
            if await db.try_consume("gemini", db.key_id(k), today, s.gemini_daily_cap):
                self._cursor = (self.keys.index(k) + 1) % len(self.keys)
                return k
        raise QuotaExhausted("All Gemini keys hit today's safety cap")

    async def generate_json(self, system: str, prompt: str, max_tokens: int = 1024) -> dict:
        if not self.keys:
            raise QuotaExhausted("No GEMINI_API_KEYS configured")
        model = await self.resolve_model()
        s = get_settings()
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"},
        }
        if self._think_off is not False:   # extraction needs no reasoning: thinking only adds latency and tokens
            body["generationConfig"]["thinkingConfig"] = _think_cfg(model)
        last_err: Exception | None = None
        for attempt in range(len(self.keys) + 1):
            key = await self._acquire_key()
            async with self._locks[key]:
                wait = s.gemini_min_interval_s - (time.monotonic() - self._last_call[key])
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_call[key] = time.monotonic()
                r = await self.http.post(f"{API}/models/{model}:generateContent", json=body,
                                         headers={"x-goog-api-key": key})
            if r.status_code == 400 and "thinking" in r.text.lower() and self._think_off is not False:
                self._think_off = False            # this model can't switch thinking off: send plain requests
                body["generationConfig"].pop("thinkingConfig", None)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err = RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
                if r.status_code == 429:
                    self._cooldown_until[key] = time.monotonic() + 60
                log.warning("Gemini %s on key %s, rotating", r.status_code, db.key_id(key))
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (400, 401, 403) and re.search(r"api[ _-]?key|permission|unauthenticated", r.text, re.I):
                raise KeyRejected("The Gemini API key was rejected. Check it at aistudio.google.com/apikey.")
            r.raise_for_status()
            data = r.json()
            try:
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError) as e:
                raise RuntimeError(f"Empty Gemini response: {str(data)[:300]}") from e
            return _parse_json(text)
        raise QuotaExhausted(str(last_err))


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


gemini: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    """The visitor's own client during a BYOK run, otherwise the server's (private instances only)."""
    rk = run_keys()
    if rk is not None:
        if "client" not in rk:
            rk["client"] = GeminiClient([rk["gemini"]])
        return rk["client"]
    global gemini
    if gemini is None:
        gemini = GeminiClient()
    return gemini


async def close_run_client() -> None:
    rk = run_keys()
    if rk and "client" in rk:
        await rk.pop("client").http.aclose()
