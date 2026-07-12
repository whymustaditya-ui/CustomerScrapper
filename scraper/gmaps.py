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
import re
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
    place_id: str = ""
    query_area: str = ""
    skipped_fields: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["skipped_fields"] = ",".join(self.skipped_fields)
        return d


def _noop(msg: str, frac: float) -> None:
    pass


def human_delay(low: float, high: float) -> float:
    """A human-like pause length in [low, high], NOT flat-uniform.

    Real people cluster: most actions are quick with occasional long pauses
    (reading a listing, getting distracted). We draw from a triangular distro
    with the mode near the low end (fast bias + long tail), and ~10% of the time
    stretch past `high` for a genuine "stepped away" pause. This is the pacing
    Bro's senior flagged — randomized between x and y to match human behaviour —
    refined so the distribution shape itself looks human, not just the bounds.
    """
    if high <= low:
        return max(low, 0.0)
    mode = low + 0.25 * (high - low)
    val = random.triangular(low, high, mode)
    if random.random() < 0.10:  # occasional long "distracted" pause
        val = random.uniform(high, high * 1.6)
    return val


def _sleep_human(low: float, high: float) -> None:
    time.sleep(human_delay(low, high))


def _apply_stealth(page) -> bool:
    """Apply playwright-stealth if available; tolerate version/API differences.

    Masks automation fingerprints (navigator.webdriver, etc.) that Google's bot
    detection targets. Degrades to a no-op (returns False) if the package isn't
    installed — the scrape still runs, just more detectable.
    """
    try:
        from playwright_stealth import stealth_sync  # older API (<2.0)
        stealth_sync(page)
        return True
    except Exception:
        pass
    try:
        from playwright_stealth import Stealth  # newer API (>=2.0)
        Stealth().apply_stealth_sync(page)
        return True
    except Exception:
        pass
    return False


_CURSOR_JS = """
() => {
  if (document.getElementById('__bot_cursor')) return;
  const c = document.createElement('div');
  c.id = '__bot_cursor';
  c.style.cssText = 'position:fixed;z-index:2147483647;width:18px;height:18px;'
    + 'margin:-9px 0 0 -9px;border-radius:50%;background:rgba(66,133,244,.65);'
    + 'border:2px solid #1a73e8;box-shadow:0 0 8px rgba(26,115,232,.8);'
    + 'pointer-events:none;transition:left .04s linear,top .04s linear;'
    + 'left:50%;top:50%;';
  document.body.appendChild(c);
}
"""


def _inject_cursor(page) -> None:
    """Add a visible cursor dot to the page (Watch Mode only)."""
    try:
        page.evaluate(_CURSOR_JS)
    except Exception:
        pass


def _move_cursor(page, x: float, y: float, steps: int = 22) -> None:
    """Animate the visible overlay + drive the real Playwright mouse to (x, y).

    The overlay is what you *see* in Watch Mode; page.mouse.move fires the real
    pointer events Google scores. Both, in human-eased steps. Fully best-effort —
    any failure here must never break the scrape.
    """
    try:
        box = page.evaluate("() => { const c=document.getElementById('__bot_cursor');"
                            "return c ? {x: parseFloat(c.style.left)||0, y: parseFloat(c.style.top)||0} : {x:0,y:0}; }")
        sx, sy = float(box.get("x", 0)), float(box.get("y", 0))
    except Exception:
        sx, sy = 0.0, 0.0
    for i in range(1, steps + 1):
        # ease-in-out for natural acceleration
        t = i / steps
        ease = t * t * (3 - 2 * t)
        cx = sx + (x - sx) * ease
        cy = sy + (y - sy) * ease
        try:
            page.evaluate(
                "([x,y]) => { const c=document.getElementById('__bot_cursor');"
                "if(c){c.style.left=x+'px';c.style.top=y+'px';} }",
                [cx, cy],
            )
            page.mouse.move(cx, cy)
        except Exception:
            break
        time.sleep(random.uniform(0.008, 0.022))


def _cursor_to_element(page, el) -> None:
    """Move the visible cursor to an element's center and hover it."""
    try:
        box = el.bounding_box()
        if not box:
            return
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        _move_cursor(page, cx, cy)
        el.hover(timeout=2000)
    except Exception:
        pass


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


def extract_place_id(url: str) -> str:
    """Extract Google's stable place identity from a Maps URL.

    The canonical feature/CID id is a hex pair like `0x2e69f...:0x8a3b...` that
    appears after `!1s` in the URL and never changes for a location — the gold
    standard dedup key. Falls back to the `!19s` ChIJ-style token, then to the
    `/place/<slug>/` path segment so we always return *something* stable-ish.
    """
    if not url:
        return ""
    # Primary: the 0x..:0x.. feature id (most stable).
    m = re.search(r"(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", url)
    if m:
        return m.group(1).lower()
    # Secondary: ChIJ-style place id token after !19s or place_id=.
    m = re.search(r"!19s([A-Za-z0-9_\-]+)", url) or re.search(r"place_id[=:]([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1)
    # Tertiary: the place slug in the path.
    m = re.search(r"/place/([^/@]+)", url)
    if m:
        return "slug:" + m.group(1).lower()
    return ""


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

    # Stable Google identity: the feature/CID hex pair in the canonical URL.
    # Prefer the live page URL (richer after navigation), fall back to the link.
    place.place_id = extract_place_id(page.url) or extract_place_id(place.place_url)
    if not place.place_id:
        place.skipped_fields.append("place_id")

    return place


def scrape(
    kota: str,
    kecamatan_list: list[str],
    buckets: list[str],
    max_results: int = 50,
    delay_min: float = 2.0,
    delay_max: float = 8.0,
    scroll_min: float = 0.8,
    scroll_max: float = 2.0,
    headless: bool = True,
    watch_mode: bool = False,
    progress: ProgressFn = _noop,
    on_lead: Optional[Callable[[dict], None]] = None,
    target_qualified: Optional[int] = None,
    qualifies: Optional[Callable[[dict], bool]] = None,
) -> list[dict]:
    """Run the scrape grid and return a list of place dicts.

    Args:
      kota: city name used in the search query.
      kecamatan_list: kecamatan to iterate for coverage (use [""] for kota-only).
      buckets: industry buckets from categories.py (each expands to synonyms).
      max_results: hard safety cap on total places scanned this run. When
        target_qualified is set this is only a ceiling (stop-at-target wins first);
        without a target it's the plain collection cap (old behaviour).
      delay_min/delay_max: human pause bounds (s) between places (non-uniform draw).
      scroll_min/scroll_max: human pause bounds (s) between feed scrolls.
      headless: run browser headless (ignored — forced visible — when watch_mode).
      watch_mode: visible browser + simulated cursor + human pacing (anti-block).
      progress: callback(message, fraction 0..1) for UI updates.
      on_lead: optional callback(lead_dict) fired as each lead is collected (live UI).
      target_qualified: if set, keep scraping until this many *qualified* leads are
        collected (per `qualifies`), instead of stopping at max_results raw. Still
        bounded by max_results as an absolute anti-block ceiling.
      qualifies: predicate(place_dict) -> bool deciding if a scraped lead counts
        toward target_qualified (e.g. has a WA number, passes review floor, net-new).
    """
    if watch_mode:
        headless = False  # the whole point of watch mode is to see it

    results: list[Place] = []
    seen_urls: set[str] = set()
    qualified = 0  # count of leads passing `qualifies` (drives target-based stop)

    def _reached_goal() -> bool:
        """Stop condition. Target-based when a target is set (raw cap is the ceiling),
        else the plain raw cap. Keeps 'scrape until N good leads' from over-running."""
        if len(results) >= max_results:
            return True  # absolute anti-block ceiling, always wins
        if target_qualified is not None:
            return qualified >= target_qualified
        return False

    # Build the query grid: every (synonym, area) pair, then shuffle so the run
    # order isn't a predictable bot signature (session-level randomization).
    areas = kecamatan_list or [""]
    query_grid: list[tuple[str, str, str]] = []  # (term, bucket, area_label)
    for bucket in buckets:
        for term in cat_mod.synonyms(bucket):
            for kec in areas:
                area_label = f"{kec} {kota}".strip() if kec else kota
                query_grid.append((term, bucket, area_label))
    random.shuffle(query_grid)

    total_steps = max(len(query_grid), 1)

    def _emit(place: Place) -> None:
        nonlocal qualified
        results.append(place)
        d = place.as_dict()
        if qualifies is not None:
            try:
                if qualifies(d):
                    qualified += 1
            except Exception:
                pass
        if on_lead:
            try:
                on_lead(d)
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="id-ID",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        _apply_stealth(page)

        try:
            for i, (term, bucket, area_label) in enumerate(query_grid):
                if _reached_goal():
                    break
                frac = i / total_steps
                if target_qualified is not None:
                    progress(
                        f"Searching: {term} — {area_label} "
                        f"({qualified}/{target_qualified} qualified, {len(results)} scanned)",
                        frac,
                    )
                else:
                    progress(f"Searching: {term} — {area_label} ({len(results)} leads so far)", frac)

                query = f"{term} {area_label}".replace(" ", "+")
                url = f"https://www.google.com/maps/search/{query}"
                try:
                    page.goto(url, timeout=45000, wait_until="domcontentloaded")
                except PWTimeout:
                    progress(f"Timeout loading {area_label}; skipping", frac)
                    continue

                _dismiss_consent(page)
                if watch_mode:
                    _inject_cursor(page)

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
                            _emit(place)
                    continue

                # Scroll the feed to load more cards.
                _scroll_feed(
                    page, max_cards=max_results - len(results),
                    scroll_min=scroll_min, scroll_max=scroll_max, watch_mode=watch_mode,
                )

                # Collect place links from the feed.
                links = page.query_selector_all('div[role="feed"] a[href*="/maps/place/"]')
                place_urls = []
                for a in links:
                    href = a.get_attribute("href")
                    if href and href not in seen_urls:
                        place_urls.append(href)

                # Watch mode: a visible "review" pass — hover the cards we're about to
                # work so you can watch the robot move down the list. Done while the
                # feed is still present; data retrieval below stays on reliable goto.
                if watch_mode:
                    for a in links[:min(len(place_urls), 6)]:
                        _cursor_to_element(page, a)
                        time.sleep(random.uniform(0.1, 0.4))

                for href in place_urls:
                    if _reached_goal():
                        break
                    seen_urls.add(href)
                    try:
                        page.goto(href, timeout=30000, wait_until="domcontentloaded")
                        _dismiss_consent(page)
                        page.wait_for_selector("h1", timeout=10000)
                    except PWTimeout:
                        continue

                    if watch_mode:
                        _inject_cursor(page)
                        title = page.query_selector("h1")
                        if title:
                            _cursor_to_element(page, title)

                    place = Place(
                        matched_term=term, industry=bucket, query_area=area_label,
                        place_url=href,
                    )
                    _extract_detail(page, place)
                    if place.name:
                        _emit(place)
                    _sleep_human(delay_min, delay_max)

                # Occasional session-level noise: a brief idle between queries.
                if random.random() < 0.25:
                    _sleep_human(delay_min, delay_max)

            if target_qualified is not None:
                progress(
                    f"Done. {qualified}/{target_qualified} qualified "
                    f"({len(results)} scanned).", 1.0,
                )
            else:
                progress(f"Done. {len(results)} leads collected.", 1.0)
        finally:
            context.close()
            browser.close()

    return [r.as_dict() for r in results]


def _scroll_feed(
    page, max_cards: int, scroll_min: float, scroll_max: float, watch_mode: bool = False
) -> None:
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
            if watch_mode:
                # Smaller incremental scrolls look human; one big jump does not.
                page.eval_on_selector(feed_sel, "el => el.scrollBy(0, el.clientHeight * 0.8)")
                # ~15% of the time, nudge back up a touch (real people do this).
                if random.random() < 0.15:
                    page.eval_on_selector(feed_sel, "el => el.scrollBy(0, -60)")
            else:
                page.eval_on_selector(feed_sel, "el => el.scrollBy(0, el.scrollHeight)")
        except Exception:
            break
        _sleep_human(scroll_min, scroll_max)
