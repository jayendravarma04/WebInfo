"""Headless Chromium navigator.

One shared browser + context (memory-light for an e2-micro), one page at a time.
Every visit streams screenshots and cursor coordinates so the UI can replay the
agent "moving" over the site. Coordinates are in screenshot pixels (viewport space).
"""
import asyncio
import base64
import ipaddress
import logging
import random
import socket
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import get_settings

log = logging.getLogger("webinfo.browser")
Emit = Callable[[str, dict], Awaitable[None]]

BLOCKED_TYPES = {"media", "font", "websocket", "eventsource", "manifest"}
# Bot walls / error pages: treat as a failed visit so the agent moves to the next result.
BLOCK_MARKERS = ("access denied", "403 forbidden", "are you a robot", "confirm that you", "verify you are human",
                 "captcha", "just a moment", "attention required", "enable javascript and cookies",
                 "unusual traffic", "request blocked", "page not found", "404 not found")
AD_HOSTS = ("doubleclick", "googlesyndication", "adservice", "taboola", "outbrain", "criteo",
            "scorecardresearch", "hotjar", "facebook.net", "amazon-adsystem")

EXTRACT_JS = r"""
(maxChars) => {
  const sel = 'h1,h2,h3,h4,p,li,td,th,dt,dd,pre,blockquote,figcaption,summary,[role=heading]';
  const out = []; let total = 0; let id = 0;
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('nav,footer,header,aside,form,[aria-hidden=true],script,style,noscript')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    let t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    const isHead = /^H\d$/.test(el.tagName) || el.getAttribute('role') === 'heading';
    if (!t || (!isHead && t.length < 25) || seen.has(t)) continue;
    // skip containers whose text is mostly a child block we will also capture
    if (el.tagName === 'LI' && el.querySelector('p,li')) continue;
    seen.add(t);
    if (t.length > 700) t = t.slice(0, 700) + '…';
    total += t.length;
    if (total > maxChars) break;
    out.push({id: id++, tag: el.tagName.toLowerCase(), text: t,
              x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY),
              w: Math.round(r.width), h: Math.round(r.height)});
  }
  const links = [];
  const here = location.hostname;
  for (const a of document.querySelectorAll('a[href]')) {
    const txt = (a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!txt || txt.length < 3 || txt.length > 120) continue;
    let u; try { u = new URL(a.href); } catch { continue; }
    if (!/^https?:$/.test(u.protocol) || u.href === location.href) continue;
    links.push({text: txt, href: u.href.split('#')[0], same: u.hostname === here});
    if (links.length > 150) break;
  }
  const uniq = [...new Map(links.map(l => [l.href, l])).values()]
      .sort((a, b) => (b.same - a.same)).slice(0, 40);
  return {title: document.title, blocks: out, links: uniq,
          pageH: document.documentElement.scrollHeight};
}
"""

FOCUS_JS = r"""
([needle, vh]) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const n = norm(needle).slice(0, 80);
  let best = null;
  for (const el of document.querySelectorAll('h1,h2,h3,h4,p,li,td,th,dt,dd,pre,blockquote,span,div')) {
    const t = norm(el.innerText);
    if (!t.includes(n)) continue;
    if (!best || t.length < norm(best.innerText).length) best = el;   // tightest match
  }
  if (!best) return null;
  const r0 = best.getBoundingClientRect();
  window.scrollTo(0, Math.max(0, r0.top + scrollY - vh * 0.35));
  const r = best.getBoundingClientRect();
  return {x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height)};
}
"""


@dataclass
class PageData:
    page_id: str
    url: str
    title: str = ""
    blocks: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    error: str | None = None


_host_ok: dict[str, bool] = {}
_robots: dict[str, RobotFileParser | None] = {}
ROBOTS_UA = "WebInfoAgent"


async def robots_allowed(url: str) -> bool:
    """Honour robots.txt for our agent token (falls back to '*'). Unreachable robots.txt = allowed."""
    p = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in _robots:
        rp: RobotFileParser | None = None
        try:
            async with httpx.AsyncClient(timeout=4, follow_redirects=True,
                                         headers={"User-Agent": f"{ROBOTS_UA}/1.0"}) as http:
                r = await http.get(origin + "/robots.txt")
            if r.status_code == 200 and len(r.text) < 500_000:
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
            elif r.status_code in (401, 403):
                rp = RobotFileParser()
                rp.parse(["User-agent: *", "Disallow: /"])
        except Exception:  # noqa: BLE001
            rp = None
        _robots[origin] = rp
    rp = _robots[origin]
    return True if rp is None else rp.can_fetch(ROBOTS_UA, url)


def _is_public_host(host: str) -> bool:
    """SSRF guard: never let the agent reach the VM metadata server or private networks."""
    if host in ("metadata.google.internal", "metadata", "169.254.169.254"):
        return False
    if get_settings().allow_private_hosts:
        return True
    if not host or host == "localhost":
        return False
    if host in _host_ok:
        return _host_ok[host]
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
        ok = all(ipaddress.ip_address(a).is_global for a in addrs)
    except Exception:  # noqa: BLE001
        ok = False
    _host_ok[host] = ok
    return ok


def is_allowed_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and _is_public_host(p.hostname or "")


class Navigator:
    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None
        self._sem = asyncio.Semaphore(1)   # e2-micro: exactly one tab at a time

    async def start(self) -> None:
        s = get_settings()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-http2",
                  "--disable-extensions", "--mute-audio", "--no-zygote",
                  "--renderer-process-limit=2", "--js-flags=--max-old-space-size=256"],
        )
        self._ctx = await self._browser.new_context(
            viewport={"width": s.viewport_w, "height": s.viewport_h},
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/141.0.0.0 Safari/537.36 WebInfoAgent/1.0"),
            locale="en-US",
            java_script_enabled=True,
        )
        await self._ctx.route("**/*", self._route)

    async def stop(self) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def _route(self, route):
        req = route.request
        host = urlparse(req.url).hostname or ""
        if (req.resource_type in BLOCKED_TYPES or any(a in host for a in AD_HOSTS)
                or (req.url.startswith("http") and not await asyncio.to_thread(_is_public_host, host))):
            await route.abort()
        else:
            await route.continue_()

    # ---------- helpers ----------
    async def _shot(self, page: Page, page_id: str, emit: Emit) -> None:
        s = get_settings()
        img = await page.screenshot(type="jpeg", quality=s.screenshot_quality, full_page=False,
                                    timeout=8000, animations="disabled", caret="hide")
        await emit("screenshot", {
            "page_id": page_id, "url": page.url,
            "image": "data:image/jpeg;base64," + base64.b64encode(img).decode(),
            "width": s.viewport_w, "height": s.viewport_h,
            "scroll_y": await page.evaluate("scrollY"),
        })

    async def _dismiss_banners(self, page: Page) -> None:
        for label in ("Accept all", "Accept", "I agree", "Agree", "Got it", "OK"):
            try:
                btn = page.get_by_role("button", name=label, exact=False).first
                if await btn.is_visible(timeout=250):
                    await btn.click(timeout=800)
                    return
            except Exception:  # noqa: BLE001
                continue

    async def _open(self, url: str) -> Page:
        s = get_settings()
        page = await self._ctx.new_page()
        page.set_default_timeout(s.page_timeout_ms)
        await page.goto(url, wait_until="domcontentloaded", timeout=s.page_timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:  # noqa: BLE001
            pass
        return page

    # ---------- public ----------
    async def visit(self, page_id: str, url: str, emit: Emit) -> PageData:
        s = get_settings()
        if not is_allowed_url(url):
            return PageData(page_id, url, error="blocked host")
        if not await robots_allowed(url):
            await emit("page_error", {"page_id": page_id, "url": url,
                                      "message": "Site's robots rules ask agents not to read this page"})
            return PageData(page_id, url, error="robots")
        async with self._sem:
            await emit("navigate", {"page_id": page_id, "url": url})
            page = None
            try:
                page = await self._open(url)
                await self._dismiss_banners(page)
                await self._shot(page, page_id, emit)
                data = await page.evaluate(EXTRACT_JS, s.page_text_chars)

                # "Reading" sweep: glide the cursor across headings/paragraphs in the first screen,
                # then scroll once and repeat, so the UI can animate a scanning cursor.
                for screen in range(2):
                    sy = await page.evaluate("scrollY")
                    vis = [b for b in data["blocks"]
                           if sy <= b["y"] < sy + s.viewport_h - 40][:5]
                    for b in vis:
                        await emit("cursor", {
                            "page_id": page_id, "action": "hover",
                            "x": min(b["x"] + random.randint(10, 60), s.viewport_w - 10),
                            "y": b["y"] - sy + min(b["h"] // 2, 18),
                            "label": b["text"][:60],
                        })
                        await asyncio.sleep(0.12)
                    if screen == 0 and data["pageH"] > s.viewport_h * 1.3:
                        await emit("cursor", {"page_id": page_id, "action": "scroll",
                                              "x": s.viewport_w // 2, "y": s.viewport_h - 60})
                        await page.mouse.wheel(0, int(s.viewport_h * 0.8))
                        await asyncio.sleep(0.35)
                        await self._shot(page, page_id, emit)
                    else:
                        break

                probe = " ".join([data["title"]] + [b["text"] for b in data["blocks"][:3]]).lower()
                if ("/blocked" in page.url or len(data["blocks"]) < 3
                        or any(m in probe for m in BLOCK_MARKERS)):
                    await emit("page_error", {"page_id": page_id, "url": page.url,
                                              "message": "Site blocked automated reading or had no content"})
                    return PageData(page_id, page.url, data["title"], error="blocked")
                pd = PageData(page_id, page.url, data["title"], data["blocks"], data["links"])
                await emit("page_read", {"page_id": page_id, "url": pd.url, "title": pd.title,
                                         "blocks": len(pd.blocks), "links": len(pd.links)})
                return pd
            except Exception as e:  # noqa: BLE001
                log.info("visit failed %s: %s", url, e)
                await emit("page_error", {"page_id": page_id, "url": url, "message": str(e)[:160]})
                return PageData(page_id, url, error=str(e)[:200])
            finally:
                if page:
                    await page.close()

    async def focus(self, page_id: str, url: str, quote: str, emit: Emit) -> bool:
        """Re-open the cited page, scroll to the evidence, move cursor there and highlight it."""
        if not is_allowed_url(url):
            return False
        async with self._sem:
            page = None
            try:
                await emit("navigate", {"page_id": page_id, "url": url, "reason": "evidence"})
                page = await self._open(url)
                await self._dismiss_banners(page)
                box = await page.evaluate(FOCUS_JS, [quote, get_settings().viewport_h])
                await asyncio.sleep(0.25)
                await self._shot(page, page_id, emit)
                if not box:
                    return False
                await emit("cursor", {"page_id": page_id, "action": "move",
                                      "x": box["x"] + 8, "y": box["y"] + min(box["h"] // 2, 14)})
                await emit("cursor", {"page_id": page_id, "action": "select",
                                      "x": box["x"] + min(box["w"], 600), "y": box["y"] + box["h"]})
                await emit("highlight", {"page_id": page_id, **box, "quote": quote[:300]})
                return True
            except Exception as e:  # noqa: BLE001
                log.info("focus failed %s: %s", url, e)
                return False
            finally:
                if page:
                    await page.close()


navigator = Navigator()
