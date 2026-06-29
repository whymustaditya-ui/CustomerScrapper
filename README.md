# ROSH Super Customer Scraper

Google Maps B2B lead-gen for ROSH packaging (Thinwall, Cup Oz). Scrapes catering /
horeca / resto listings in Jabodetabek, parses address to kelurahan/kota, estimates
store size, dedups against existing customers + prior runs, and exports a clean,
segmented list for Nathan to work via Mekari Qontak.

> ⚠️ **Do not run on MoF / DJP infrastructure.** GMaps scraping breaches Google ToS
> and risks IP blocks. Run from a personal/business network only. Keep volume modest,
> respect the throttle, and treat outreach as opt-out-respecting (protects Nathan's
> WhatsApp number from bans).

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
streamlit run app.py
```

Then in the browser:

1. **Sidebar** — pick Kota (default Jakarta Selatan), industry category (default
   Catering), max results, throttle, headless toggle.
2. (Optional) upload an **existing-customer CSV/Excel** to exclude current customers.
3. Keep **Filter against master ledger** on so weekly re-runs only surface *new* leads.
4. **▶ Run scrape** → watch progress → review the sortable results table.
5. **Download** xlsx (multi-sheet: Leads · Excluded-Dedup · Run summary) or CSV
   (Qontak-import column order). Files also save to `data/output/`.
6. **Push to Qontak** — dry-run (logs payloads) until you add credentials.

## Pilot validation (v1)

Target: **Jakarta Selatan + Catering**, ~30–50 leads. The two things that can only be
proven by running it:

- **Scraper reliability** — does GMaps return data without blocking? If selectors are
  flaky, timebox the fight; the `scrape()` signature in `scraper/gmaps.py` can be
  swapped for the Google Places API with nothing else changing.
- **Store-size scoring** — sanity-check that top-scored leads are visibly bigger than
  bottom-scored. `review_count` proxies *popularity*, not packaging volume, so treat
  the score as a rough sort and tune `WEIGHTS` in `enrich/scoring.py` after the pilot.

Also check: phone hit-rate (target >70%, may be lower for IG/WA-only caterers),
kelurahan parse accuracy (spot-check 10), dedup excludes + flags matches, xlsx opens
clean, Qontak dry-run logs a correct payload.

## Activate Qontak

```bash
cp config/.env.example config/.env
# fill in QONTAK_CLIENT_ID / SECRET / USERNAME / PASSWORD / BASE_URL / AUTH_URL
```

Re-run, push a 3–5 lead test batch, confirm contacts appear in Qontak. Verify the
endpoint path + field mapping in `integrations/qontak.py` against current Qontak docs.

## Structure

```
app.py                  Streamlit UI (filters, run, review, export, push)
scraper/gmaps.py        Playwright search + scroll + detail extraction
scraper/categories.py   search-query synonyms per industry bucket
scraper/areas.py        Jabodetabek kota/kecamatan reference
enrich/parser.py        address -> kelurahan/kota; phone/website/name normalization
enrich/scoring.py       store-size estimate (Kecil/Sedang/Besar) — tunable WEIGHTS
enrich/dedup.py         internal + customer-list + master-ledger dedup
integrations/qontak.py  OAuth + contact push, gated on config/.env
output/exporter.py      xlsx (3 sheets) + Qontak-friendly csv
data/output/            generated lead files
data/ledger.csv         master ledger of all leads ever surfaced (auto-created)
```
