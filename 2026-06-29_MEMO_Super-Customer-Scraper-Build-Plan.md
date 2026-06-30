# ROSH Super Customer Scraper — Build Plan

## Context

ROSH (food packaging distributor — Thinwall, Cup Oz) needs a steady pipeline of new B2B
customers: catering, horeca, and restos in Jabodetabek that plausibly use disposable
packaging. Today there's no systematic lead-gen — leads are ad hoc. This tool scrapes
Google Maps to build qualified lead lists, scores each by likely volume ("store size"),
dedups against existing ROSH customers, and hands a clean, segmented list to Sales to
work via Mekari Qontak (WhatsApp Business outreach + website pitch).

**Goal per lead:** Name · Industry · Location (Kelurahan + Kota) · Reliable contact
(phone/WA + website) · Store-size estimate.

**Decisions locked with Bro:**
- Source: **scrape Google Maps directly** (Playwright headless browser) — no paid API.
- Delivery: **Streamlit local web UI** (pick area + category, run, review, download).
- Scope: **pilot first** — one area + one category to prove data quality before scaling.
- Output: **both** — Excel/CSV export (day one) + Mekari Qontak push.
- Qontak: build the push module **behind a config stub**; activates once Bro adds API keys.
- Dedup: **against an uploaded existing-customer list** (CSV/Excel) + internal dedup.

**Operational note (not optional):** GMaps scraping breaches Google ToS and risks IP
blocks. Mitigate with conservative rate-limiting, random delays, realistic UA, modest
per-run volume (pilot scope helps). Run from a personal/business network, **not** MoF
infrastructure. Outreach must be opt-out-respecting and not a cold blast (protects
Sales' WA number from bans).

## Tech Stack

Python 3.11+ · Playwright (scraping) · Streamlit (UI) · pandas + openpyxl (data/export)
· requests + python-dotenv (Qontak + config). Matches Bro's Python analytics stack;
re-runnable weekly.

## Project Structure

```
Customer Scrapper/
├── app.py                      # Streamlit UI (filters, run, review, export, push)
├── requirements.txt
├── README.md
├── config/
│   └── .env.example            # QONTAK_CLIENT_ID / SECRET / BASE_URL placeholders
├── scraper/
│   ├── gmaps.py                # Playwright search + scroll + detail extraction
│   ├── categories.py           # search-query templates per industry
│   └── areas.py                # Jabodetabek kota/kecamatan reference for queries
├── enrich/
│   ├── parser.py               # address -> kelurahan/kota; field normalization
│   ├── scoring.py              # store-size estimate (Kecil/Sedang/Besar + score)
│   └── dedup.py                # internal dedup + match against customer list
├── integrations/
│   └── qontak.py               # OAuth token + contact push (gated on .env keys)
├── output/
│   └── exporter.py             # xlsx + csv writers (Qontak-import-friendly columns)
└── data/
    └── output/                 # generated lead files land here
```

## Component Detail

### 1. Scraper — `scraper/gmaps.py`
- Build search URL `https://www.google.com/maps/search/{query}` where query =
  `"{category} {area}"` (e.g. `"catering Jakarta Selatan"`), iterating kecamatan from
  `areas.py` for coverage.
- Launch Playwright (stealth UA, `slow_mo`, randomized waits). Scroll the results feed
  until no new cards load or `max_results` hit.
- Collect place detail URLs, then open each to extract: **name, category/industry, full
  address, phone, website, rating, review_count, price_level ($–$$$$), photo_count,
  coordinates/plus_code**. Phone + website are the "reliable contact" — Indonesian F&B
  phones are almost always WhatsApp-active.
- Robust selectors with fallbacks; skip-and-log on missing fields rather than crashing.
- Built-in throttle (configurable delay) and a hard per-run cap.

### 2. Categories — `scraper/categories.py`
Query templates per industry bucket, each a list of synonyms to widen the net:
`catering` (catering, katering, nasi box, nasi kotak), `horeca` (hotel, cafe, restoran,
kedai), `resto`, plus high-fit niches (cloud kitchen, frozen food, kue/bakery, warung
makan). Industry label is derived from which template matched + GMaps' own category text.

### 3. Parser — `enrich/parser.py`
- Indonesian GMaps addresses follow `Jl. X, RT/RW, Kelurahan, Kecamatan, Kota, Prov,
  Postcode`. Split on commas + match tokens against a kelurahan/kota reference; regex
  fallback for `Kota/Kab.` and known kecamatan from `areas.py`.
- Output clean `kelurahan` + `kota` columns; flag low-confidence parses for manual check.
- Normalize phone (strip/standardize `+62`/`0`), trim names, canonicalize website.

### 4. Store-size scoring — `enrich/scoring.py`
No public headcount/revenue, so use GMaps proxies. Weighted 0–100 score from:
`review_count` (primary volume proxy), `price_level`, `rating`, `photo_count`,
`has_website`, multi-branch signal. Bucket → **Kecil / Sedang / Besar**. Weights live in
one constant dict so Bro can tune after seeing pilot output.

### 5. Dedup — `enrich/dedup.py`
- **Internal:** drop duplicate listings by normalized phone, then by name+kelurahan
  (handles same place across multiple searches / branches).
- **Against customer list:** UI uploads existing-customer CSV/Excel; exclude matches by
  normalized phone (primary) and fuzzy name (secondary). Excluded rows are flagged in a
  separate sheet, not silently dropped, so Bro can audit.

### 6. Qontak push — `integrations/qontak.py`
- OAuth2 token (client credentials) → POST contacts to Mekari Qontak CRM, mapping our
  fields to Qontak contact fields (name, phone/WA, company, address, custom fields for
  industry + store-size + source=GMaps).
- **Gated:** if `.env` keys are absent, the module is a no-op that logs the payload it
  *would* send. Excel/CSV export is fully functional without keys. Bro drops credentials
  into `config/.env` later to go live. Push happens per-batch after Bro approves.

### 7. Export — `output/exporter.py`
- `.xlsx` (multi-sheet: Leads · Excluded/Dedup · Run summary) + `.csv` with
  Qontak-import-friendly column order. Files land in `data/output/` with timestamped
  names per ROSH convention (`YYYY-MM-DD_LEADS_{area}-{category}`).

### 8. UI — `app.py`
- Sidebar: Kota/area select, category multiselect, `max_results`, throttle/headless
  toggles, upload existing-customer file, Qontak-push toggle.
- Main: **Run** → live progress → results table (sortable by store-size score) →
  download xlsx/csv buttons → **Push approved batch to Qontak** button.

## Pilot Run (v1 validation)

Target: **Jakarta Selatan + catering** (~30–50 leads). Success checks:
1. **Phone hit-rate** — what % of leads have a usable contact (target >70%).
2. **Kelurahan accuracy** — spot-check 10 addresses parsed correctly.
3. **Store-size sanity** — top-scored leads visibly bigger than bottom-scored.
4. **Dedup** — upload a sample customer list; confirm matches are excluded + flagged.
5. **Export** — xlsx opens clean, columns map to Qontak import.
6. **Qontak stub** — push toggle logs a correct payload (no keys yet).

## Verification

- `pip install -r requirements.txt && playwright install chromium`
- `streamlit run app.py` → run the Jaksel/catering pilot end-to-end.
- Inspect generated `data/output/*.xlsx`; verify the six pilot checks above.
- Once Bro adds Qontak keys to `config/.env`, re-run push on a 3–5 lead test batch and
  confirm contacts appear in Qontak.

## Out of Scope (later phases)

- Grab Food / Go Food enrichment (harder anti-bot; GMaps covers the core for v1).
- WhatsApp-active validation of phone numbers.
- Scheduled/automated weekly runs.
- Scaling beyond pilot to full Jabodetabek (flip area/category breadth once pilot passes).
