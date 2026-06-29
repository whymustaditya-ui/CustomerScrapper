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

**Easiest (Windows):** double-click **`Start ROSH Scraper.bat`**. First run sets up a
local environment and installs everything automatically; later runs just launch.

Or manually:

```bash
streamlit run app.py
```

Then in the browser:

1. **Sidebar** — pick Kota (default Jakarta Selatan), industry category (default
   Catering), max results, **Watch Mode**, and pacing sliders.
2. (Optional) upload an **existing-customer CSV/Excel** to exclude current customers.
3. Keep **Filter against master ledger** on so weekly re-runs only surface *new* leads.
4. **▶ Run scrape** → watch progress → review the sortable results table.
5. **Download** xlsx (multi-sheet: Leads · Excluded-Dedup · Run summary) or CSV
   (Qontak-import column order). Files also save to `data/output/`. This is *Bro's*
   full-pool audit — not what Nathan works.
6. **Build Nathan's next batch (10)** — releases the top 10 highest-scored fresh leads
   into the shared Google Sheet. See "Outreach discipline" below.
7. **Push to Qontak** — dry-run (logs payloads) until you add credentials (Phase 3).

## Headless vs Watch Mode (anti-block)

Both modes extract identical data with identical code. The only differences are
speed and how likely Google is to block the run. Raw headless Playwright gets
flagged in 10–50 requests on a home IP — so the anti-block stack matters at scale.

| | Headless (default) | Watch Mode |
|---|---|---|
| Speed | Fast | 2–4× slower (human-paced) |
| Block risk | Higher | Lower — visible browser + human cursor/pacing |
| Data quality | Identical | Identical |
| Watch it work | No | Yes — animated cursor, live-streaming table |

The full stack: **non-uniform random pacing (the sliders) + `playwright-stealth`
(auto-applied if installed) + your home/residential IP + Watch Mode visibility.**
Pacing is randomized between an x–y bound *and* drawn from a human-like distribution
(fast mode, occasional long pause) — not flat-uniform. Use **headless** for fast
weekly bulk once you trust the output; flip to **Watch Mode** for the pilot, demos,
and as the fallback when headless starts getting blocked.

## Outreach discipline (why batches of 10)

Cold-blasting WhatsApp gets the number banned (Tier 1 caps + spam reports tank your
quality rating). So the system **designs scarcity in**: Nathan never sees the full pool.
He gets **10 leads at a time**, in a **shared Google Sheet**, and the **gate refuses to
release the next 10 until the current batch is worked** (every lead moved past `New`).
Blasting becomes structurally impossible — quality is the only path forward.

- **No-double guarantee:** a lead enters a batch only if its **Google place ID** (stable
  feature id, never changes) is absent from both the master ledger and the Sheet. The
  same restaurant cannot reach Nathan twice, even if its phone or name changes.
- **Each lead carries a `wa.me` click-to-chat link** — Nathan taps straight into the
  conversation. That tap is your opt-in entry point when you graduate to Qontak.
- Measure **reply-rate and deals per batch**, never messages sent.

### Google Sheet setup (one-time)

1. In Google Cloud, create a **service account** and download its JSON key.
2. Save the key as `config/service_account.json` (gitignored).
3. Create a Google Sheet; copy its id from the URL
   (`docs.google.com/spreadsheets/d/<THIS>/edit`).
4. **Share the Sheet with the service account's email** (`…@….iam.gserviceaccount.com`)
   as **Editor**.
5. Fill `GSHEETS_CREDENTIALS_FILE` + `GSHEETS_SPREADSHEET_ID` in `config/.env`.

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
enrich/dedup.py         place-id-first dedup: internal + customer-list + ledger
crm/tracker.py          batch gate — release_next_batch (10), completion check
integrations/gsheets.py gspread wrapper for Nathan's living CRM Sheet
integrations/qontak.py  OAuth + contact push, gated on config/.env (Phase 3)
output/exporter.py      xlsx (3 sheets) + Qontak-friendly csv (Bro's audit)
data/output/            generated lead files
data/ledger.csv         master ledger of all leads ever surfaced (auto-created)
```
