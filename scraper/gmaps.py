"""Google Maps scraper (Playwright headless).

Scrapes business listings for a (category synonym x area) query grid, scrolling
the results feed and opening each place's detail panel to extract contact data.

Design notes / honest caveats:
  - GMaps scraping breaches Google ToS and the DOM/selector layout changes often.
    Selectors here have fallbacks and every extraction is wrapped so a missing
    field logs-and-skips instead of crashing the run.
  - Conservative defaults: headless, randomized human-like delays, a hard per-run
    cap. Keep volume modest. Run from a personal/business network, NOT MoF infra.
  - If raw scraping proves too flaky in the pilot, swap this module for the Google
    Places API behind the same `scrape()` signature — nothing else needs to change.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from . import categories as cat_mod

# A realistic, current desktop Chrome UA.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

ProgressFn = Callable[[str, float], None]


@dataclass
class Place:
    name: str = ""
    industry: str = ""
    matched_term: str = ""
    gmaps_category: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: Optional[float] = None
    review_count: Optional[int] = None
    price_level: str = ""
    photo_count: Optional[int] = None
    plus_code: str = ""
    place_url: str = ""
    query_area: str = ""
    skipped_fields: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["skipped_fields"] = ",".join(self.skipped_fields)
        return d


def _noop(msg: str, frac: float) -> None:
    pass


def _human_pause(base: float) -> None:
    """Sleep base seconds +/- jitter to look less robotic."""
    time.sleep(base + random.uniform(0.2, base * 0.8 + 0.3))


def _safe_text(panel, selectors: Iterable[str]) -> str:
    for sel in selectors:
        try:
            el = panel.query_selector(sel)
            if el:
                txt = (el.inner_text() or "").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def _safe_attr(panel, selectors: Iterable[str], attr: str) -> str:
    for sel in selectors:
        try:
            el = panel.query_selector(sel)
            if el:
                val = el.get_attribute(attr)
                if val:
                    return val.strip()
        except Exception:
            continue
    return ""


def _parse_int(s: str) -> Optional[int]:
    if not s:
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else None


def _parse_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.replace(",", ".")
    buf = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            buf += ch
        elif buf:
            break
    try:
        return float(buf) if buf else None
    except ValueError:
        return None


def _dismiss_consent(page) -> None:
    """Google often shows a consent interstitial before Maps loads."""
    try:
        # Common consent buttons across locales.
        for sel in [
            'button[aria-label*="Accept all"]',
            'button[aria-label*="Reject all"]',
            'button:has-text("Accept all")',
            'button:has-text("Terima semua")',
            'form[action*="consent"] button',
        ]:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                page.wait_for_timeout(1500)
                return
    except Exception:
        pass


def _extract_detail(page, place: Place) -> Place:
    """Extract fields from an open place detail panel."""
    place.name = _safe_text(page, ["h1.DUwDvf", "h1.fontHeadlineLarge", "h1"]) or place.name
    if not place.name:
        place.skipped_fields.append("name")

    place.gmaps_category = _safe_text(page, ["button.DkEaL", 'button[jsaction*="category"]'])

    # Address / phone / website use stable data-item-id anchors.
    place.address = _safe_text(
        page,
        ['button[data-item-id="address"]', 'button[data-tooltip="Copy address"]'],
    )
    if not place.address:
        place.skipped_fields.append("address")

    place.phone = _safe_text(
        page,
        ['button[data-item-id^="phone"]', 'button[data-tooltip="Copy phone number"]'],
    )
    if not place.phone:
        place.skipped_fields.append("phone")

    place.website = _safe_attr(
        page,
        ['a[data-item-id="authority"]', 'a[data-tooltip="Open website"]'],
        "href",
    )

    place.plus_code = _safe_text(page, ['button[data-item-id="oloc"]'])

    # Rating + review count live in the F7nice block.
    rating_txt = _safe_text(page, ['div.F7nice span[aria-hidden="true"]', 'div.F7nice'])
    place.rating = _parse_float(rating_txt)
    review_txt = _safe_text(
        page,
        ['div.F7nice span[aria-label*="review"]', 'div.F7nice span[aria-label*="ulasan"]'],
    )
    place.review_count = _parse_int(review_txt)

    # Price level: count $ or detect Rp ranges in the header chips.
    price_txt = _safe_text(
        page,
        ['span[aria-label*="Price"]', 'span[aria-label*="Harga"]', "span.mgr77e"],
    )
    if price_txt:
        dollars = price_txt.count("$")
        place.price_level = "$" * dollars if dollars else price_txt.strip()

    return place


def scrape(
    kota: str,
    kecamatan_list: list[str],
    buckets: list[str],
    max_results: int = 50,
    throttle: float = 2.0,
    headless: bool = True,
    progress: ProgressFn = _noop,
) -> list[dict]:
    """Run the scrape grid and return a list of place dicts.

    Args:
      kota: city name used in the search query.
      kecamatan_list: kecamatan to iterate for coverage (use [""] for kota-only).
      buckets: industry buckets from categories.py (each expands to synonyms).
      max_results: hard cap on total places collected this run.
      throttle: base delay (s) between actions; jitter is added on top.
      headless: run browser headless.
      progress: callback(message, fraction 0..1) for UI updates.
    """
    results: list[Place] = []
    seen_urls: set[str] = set()

    # Build the query grid: every (synonym, area) pair.
    areas = kecamatan_list or [""]
    query_grid: list[tuple[str, str, str]] = []  # (term, bucket, area_label)
    for bucket in buckets:
        for term in cat_mod.synonyms(bucket):
            for kec in areas:
                area_label = f"{kec} {kota}".strip() if kec else kota
                query_grid.append((term, bucket, area_label))

    total_steps = max(len(query_grid), 1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="id-ID",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()

        try:
            for i, (term, bucket, area_label) in enumerate(query_grid):
                if len(results) >= max_results:
                    break
                frac = i / total_steps
                progress(f"Searching: {term} — {area_label} ({len(results)} leads so far)", frac)

                query = f"{term} {area_label}".replace(" ", "+")
                url = f"https://www.google.com/maps/search/{query}"
                try:
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                except PWTimeout:
                    progress(f"Timeout loading {area_label}; skipping", frac)
                    continue

                _dismiss_consent(page)

                # Wait for the results feed; if it never appears, skip this query.
                try:
                    page.wait_for_selector('div[role="feed"]', timeout=15000)
                except PWTimeout:
                    # Could be a single direct result, or a block. Try detail extract.
                    if "/maps/place/" in page.url:
                        place = Place(
                            matched_term=term, industry=bucket, query_area=area_label,
                            place_url=page.url,
                        )
                        _extract_detail(page, place)
                        if place.name and page.url not in seen_urls:
                            seen_urls.add(page.url)
                            results.append(place)
                    continue

                # Scroll the feed to load more cards.
                _scroll_feed(page, max_cards=max_results - len(results), throttle=throttle)

                # Collect place links from the feed.
                links = page.query_selector_all('div[role="feed"] a[href*="/maps/place/"]')
                place_urls = []
                for a in links:
                    href = a.get_attribute("href")
                    if href and href not in seen_urls:
                        place_urls.append(href)

                for href in place_urls:
                    if len(results) >= max_results:
                        break
                    seen_urls.add(href)
                    try:
                        page.goto(href, timeout=30000, wait_until="domcontentloaded")
                        _dismiss_consent(page)
                        page.wait_for_selector("h1", timeout=10000)
                    except PWTimeout:
                        continue

                    place = Place(
                        matched_term=term, industry=bucket, query_area=area_label,
                        place_url=href,
                    )
                    _extract_detail(page, place)
                    if place.name:
                        results.append(place)
                    _human_pause(throttle)

            progress(f"Done. {len(results)} leads collected.", 1.0)
        finally:
            context.close()
            browser.close()

    return [r.as_dict() for r in results]


def _scroll_feed(page, max_cards: int, throttle: float) -> None:
    """Scroll the results feed until it stops growing or enough cards loaded."""
    feed_sel = 'div[role="feed"]'
    last_count = -1
    stagnant = 0
    for _ in range(40):  # safety bound on scroll iterations
        cards = page.query_selector_all(f'{feed_sel} a[href*="/maps/place/"]')
        count = len(cards)
        if count >= max_cards:
            break
        # "You've reached the end of the list."
        end = page.query_selector('span.HlvSq, p.fontBodyMedium span:has-text("end of the list")')
        if end:
            break
        if count == last_count:
            stagnant += 1
            if stagnant >= 3:
                break
        else:
            stagnant = 0
        last_count = count
        try:
            page.eval_on_selector(
                feed_sel,
                "el => el.scrollBy(0, el.scrollHeight)",
            )
        except Exception:
            break
        _human_pause(throttle)
