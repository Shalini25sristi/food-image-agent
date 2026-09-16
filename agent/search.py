"""Online image search with multiple providers and automatic fallback.

Provider priority:
  1. SerpAPI (Google Images)      - if SERPAPI_KEY is set
  2. Google Custom Search (CSE)   - if GOOGLE_CSE_KEY + GOOGLE_CSE_CX are set
  3. DuckDuckGo Images            - always available, no API key needed
"""

import logging
import re
import time
from dataclasses import dataclass

import requests
from urllib.parse import urlparse

from .config import Config

log = logging.getLogger(__name__)

# Stock-photo sites whose free/preview images carry visible watermarks -
# useless for a restaurant menu, so candidates from them are skipped.
_BLOCKED_DOMAINS = {
    "freepik.com", "shutterstock.com", "istockphoto.com", "gettyimages.com",
    "dreamstime.com", "123rf.com", "alamy.com", "depositphotos.com",
    "stock.adobe.com", "vecteezy.com", "pngtree.com", "canva.com",
    "stocksy.com", "agefotostock.com", "masterfile.com", "dissolve.com",
}

# Words that describe portions/variants rather than the dish itself.
_NOISE_WORDS = {
    "half", "full", "portion", "plate", "platter", "combo", "regular", "large",
    "medium", "small", "single", "double", "pc", "pcs", "piece", "pieces",
    "1/2", "1/4", "special", "sp.", "new",
}


@dataclass
class ImageCandidate:
    url: str
    width: int = 0
    height: int = 0
    source: str = ""        # search provider name
    title: str = ""

    @property
    def area(self) -> int:
        return self.width * self.height


def build_query(food_item: str) -> str:
    """Turn a raw menu name into a good image-search query.

    Handles difficult names like 'Special Paneer Tikka Masala Half' by
    removing portion/variant words and appending a food context hint.
    """
    q = re.sub(r"[()\[\],;:!?&/\\]", " ", food_item)
    q = q.split(" - ")[0]
    tokens = [t for t in q.split() if t.lower() not in _NOISE_WORDS and not t.isdigit()]
    cleaned = " ".join(tokens).strip() or food_item.strip()
    return f"{cleaned} food dish"


def _is_blocked(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return True
    return any(host == d or host.endswith("." + d) for d in _BLOCKED_DOMAINS)


def _title_relevance(title: str, query_tokens: list[str]) -> float:
    """0..1 fraction of query tokens present in the title, minus a small
    penalty for extra words (prefers 'Chicken Tikka' over 'Chicken Tikka
    Masala' when the item is 'Chicken Tikka')."""
    if not title:
        return 0.0
    t = set(re.findall(r"[a-z]+", title.lower()))
    q = {t.lower() for t in query_tokens}
    if not q:
        return 0.0
    hit = len(q & t) / len(q)
    extra = len(t - q)
    return hit - 0.03 * extra


def _sort_candidates(cands: list[ImageCandidate], cfg: Config,
                     query_tokens: list[str]) -> list[ImageCandidate]:
    """Prefer images that meet the target dimensions and whose titles match
    the food item closely."""
    def key(c: ImageCandidate):
        meets = c.width >= cfg.target_width and c.height >= cfg.target_height
        return (meets, _title_relevance(c.title, query_tokens), c.area)
    return sorted(cands, key=key, reverse=True)


class _SerpAPIProvider:
    name = "SerpAPI (Google Images)"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        return bool(self.cfg.serpapi_key)

    def search(self, query: str, limit: int) -> list[ImageCandidate]:
        resp = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "google_images",
                "q": query,
                "api_key": self.cfg.serpapi_key,
                "num": min(limit * 2, 100),
                "safe": "active",
            },
            timeout=20,
        )
        resp.raise_for_status()
        out = []
        for r in resp.json().get("images_results", []):
            out.append(ImageCandidate(
                url=r.get("original", ""),
                width=int(r.get("original_width") or 0),
                height=int(r.get("original_height") or 0),
                source=self.name,
                title=r.get("title", ""),
            ))
        return [c for c in out if c.url]


class _GoogleCSEProvider:
    name = "Google Custom Search"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        return bool(self.cfg.google_cse_key and self.cfg.google_cse_cx)

    def search(self, query: str, limit: int) -> list[ImageCandidate]:
        resp = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": self.cfg.google_cse_key,
                "cx": self.cfg.google_cse_cx,
                "q": query,
                "searchType": "image",
                "num": min(limit, 10),
                "safe": "active",
            },
            timeout=20,
        )
        resp.raise_for_status()
        out = []
        for r in resp.json().get("items", []):
            meta = r.get("image", {}) or {}
            out.append(ImageCandidate(
                url=r.get("link", ""),
                width=int(meta.get("width") or 0),
                height=int(meta.get("height") or 0),
                source=self.name,
                title=r.get("title", ""),
            ))
        return [c for c in out if c.url]


class _DuckDuckGoProvider:
    name = "DuckDuckGo Images"

    def available(self) -> bool:
        return True

    def search(self, query: str, limit: int) -> list[ImageCandidate]:
        try:
            from ddgs import DDGS  # new package name
        except ImportError:  # pragma: no cover
            from duckduckgo_search import DDGS

        last_err = None
        for attempt in range(3):
            try:
                with DDGS() as ddgs:
                    try:
                        results = ddgs.images(query, max_results=limit * 2)
                    except TypeError:  # older API used keywords=
                        results = ddgs.images(keywords=query, max_results=limit * 2)
                out = []
                for r in results or []:
                    out.append(ImageCandidate(
                        url=r.get("image", ""),
                        width=int(r.get("width") or 0),
                        height=int(r.get("height") or 0),
                        source=self.name,
                        title=r.get("title", ""),
                    ))
                return [c for c in out if c.url]
            except Exception as e:  # rate limits etc.
                last_err = e
                wait = 3 * (attempt + 1)
                log.warning("DuckDuckGo search failed (%s); retrying in %ds...", e, wait)
                time.sleep(wait)
        raise RuntimeError(f"DuckDuckGo search failed after retries: {last_err}")


def search_images(query: str, cfg: Config) -> list[ImageCandidate]:
    """Search all available providers, return de-duplicated sorted candidates."""
    providers = [_SerpAPIProvider(cfg), _GoogleCSEProvider(cfg), _DuckDuckGoProvider()]
    seen_urls: set[str] = set()
    candidates: list[ImageCandidate] = []
    query_tokens = [t for t in query.split() if t.lower() not in {"food", "dish"}]

    for p in providers:
        if not p.available():
            continue
        try:
            results = p.search(query, limit=cfg.max_candidates)
        except Exception as e:
            log.warning("Provider %s failed: %s", p.name, e)
            continue
        for c in results:
            if c.url in seen_urls or _is_blocked(c.url):
                continue
            seen_urls.add(c.url)
            candidates.append(c)
        if candidates:
            log.info("Provider %s returned %d candidate(s).", p.name, len(results))
            break  # first provider that yields results wins

    return _sort_candidates(candidates, cfg, query_tokens)
