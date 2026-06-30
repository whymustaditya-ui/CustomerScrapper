"""ROSH Super Customer Scraper — Streamlit UI.

Flow: pick area + category -> run scrape -> enrich (parse/score) -> dedup
(internal + customer list + ledger) -> review table -> download xlsx/csv ->
optionally push an approved batch to Mekari Qontak.

Run:  streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scraper import categories as cat_mod
from scraper import gmaps
from scraper.areas import KOTA_LIST, kecamatan_for
from enrich.parser import enrich_row
from enrich.scoring import score_row
from enrich import dedup as dedup_mod
from output import exporter
from integrations import qontak
from integrations import gsheets
from crm import tracker as crm_tracker
from ui import theme

st.set_page_config(page_title="ROSH Customer Scraper", page_icon="📦", layout="wide")

theme.apply_theme()
theme.hero()
theme.network_warning()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Run settings")
    kota = st.selectbox("Kota / area", KOTA_LIST, index=KOTA_LIST.index("Jakarta Selatan"))

    kecamatan_options = kecamatan_for(kota)
    use_kecamatan = st.checkbox(
        "Iterate kecamatan (wider coverage, slower)", value=False
    )
    selected_kecamatan = []
    if use_kecamatan:
        selected_kecamatan = st.multiselect(
            "Kecamatan", kecamatan_options, default=kecamatan_options[:2]
        )

    buckets = st.multiselect(
        "Industry category", cat_mod.buckets(), default=["Catering"]
    )

    max_results = st.slider("Max results (hard cap)", 10, 200, 40, step=10)

    min_reviews = st.slider(
        "Minimum reviews (legit filter)", 0, 50, 5, step=1,
        help="Drop listings with fewer reviews than this — 0-review listings are "
             "ghost/fake leads. 5 keeps real small caterers; raise to 10 for stricter.",
    )

    watch_mode = st.checkbox(
        "👁 Watch Mode (visible browser, human-paced, anti-block)", value=False,
        help="Slower but harder for Google to block, and you can watch it work. "
             "Use for the pilot and as a fallback when headless gets blocked.",
    )
    if watch_mode:
        st.caption("Watch Mode on — browser runs visible with a simulated cursor. "
                   "2–4× slower; better at avoiding blocks.")
        headless = False
    else:
        headless = st.checkbox("Headless browser (faster, more detectable)", value=True)

    st.caption("Pacing — randomized human-like delays (non-uniform).")
    delay_min, delay_max = st.slider(
        "Delay between places (s)", 0.5, 15.0, (2.0, 8.0), step=0.5,
    )
    scroll_min, scroll_max = st.slider(
        "Delay between scrolls (s)", 0.3, 5.0, (0.8, 2.0), step=0.1,
    )

    st.divider()
    st.subheader("Dedup")
    customer_file = st.file_uploader(
        "Existing-customer list (CSV/Excel)", type=["csv", "xlsx"]
    )
    use_ledger = st.checkbox(
        "Filter against master ledger (only new leads)", value=True,
        help="Excludes leads already surfaced in prior runs. Makes weekly re-runs idempotent.",
    )

    st.divider()
    st.subheader("Google Sheet")
    if gsheets.is_configured():
        if st.button("🔧 Rapikan header + isi link Maps", use_container_width=True,
                     help="Ganti header jadi label rapih (Bahasa) dan tambah kolom "
                          "Lokasi Maps untuk baris yang sudah ada. Aman diklik berulang."):
            try:
                rep = crm_tracker.repair_sheet_layout()
                st.success(f"Sheet dirapikan — {rep['rows']} baris, "
                           f"{rep['maps_links_filled']} link Maps ditambahkan.")
            except Exception as e:
                st.error(f"Gagal merapikan Sheet: {e}")
        if st.button("🔽 Pasang dropdown (Status, Follow-up, Catatan)",
                     use_container_width=True,
                     help="Status jadi pilihan tetap, Tgl Follow-up jadi date-picker "
                          "kalender, dan Catatan dapat saran cepat (tetap bisa ketik "
                          "bebas). Aman diklik berulang."):
            try:
                n = crm_tracker.apply_dropdowns()
                st.success(f"Dropdown terpasang di {n} kolom — refresh Sheet untuk lihat.")
            except Exception as e:
                st.error(f"Gagal memasang dropdown: {e}")
    else:
        st.caption("🔒 Sheet belum dikonfigurasi (lihat README).")

    st.divider()
    st.subheader("Qontak push")
    qontak_configured = qontak.is_configured()
    st.caption(
        "✅ Credentials detected — live push available."
        if qontak_configured
        else "🔒 No credentials — push runs as dry-run (logs payloads only)."
    )

    run = st.button("▶ Run scrape", type="primary", use_container_width=True)


# ---------------------------------------------------------------- helpers
def _load_customer_df(uploaded) -> pd.DataFrame | None:
    if uploaded is None:
        return None
    try:
        if uploaded.name.lower().endswith(".csv"):
            return pd.read_csv(uploaded, dtype=str).fillna("")
        return pd.read_excel(uploaded, dtype=str).fillna("")
    except Exception as e:
        st.error(f"Could not read customer file: {e}")
        return None


def _run_pipeline():
    progress_bar = st.progress(0.0)
    status = st.empty()
    live_box = st.empty()
    live_rows: list[dict] = []

    def on_progress(msg: str, frac: float):
        status.info(msg)
        progress_bar.progress(min(max(frac, 0.0), 1.0))

    def on_lead(lead: dict):
        live_rows.append({
            "name": lead.get("name", ""),
            "category": lead.get("gmaps_category", "") or lead.get("industry", ""),
            "phone": lead.get("phone", ""),
            "area": lead.get("query_area", ""),
        })
        live_box.dataframe(pd.DataFrame(live_rows), use_container_width=True, height=240)

    # 1. Scrape
    raw = gmaps.scrape(
        kota=kota,
        kecamatan_list=selected_kecamatan if use_kecamatan else [],
        buckets=buckets,
        max_results=max_results,
        delay_min=delay_min,
        delay_max=delay_max,
        scroll_min=scroll_min,
        scroll_max=scroll_max,
        headless=headless,
        watch_mode=watch_mode,
        progress=on_progress,
        on_lead=on_lead,
    )
    progress_bar.progress(1.0)
    live_box.empty()
    status.success(f"Scraped {len(raw)} raw listings. Enriching…")

    if not raw:
        st.warning("No listings returned. GMaps may have blocked the run, or the "
                   "query was too narrow. Try a different area/category or lower volume.")
        return None

    # 2. Enrich (parse + normalize)
    enriched = [enrich_row(r) for r in raw]

    # 3. Internal dedup
    unique, internal_dropped = dedup_mod.internal_dedup(enriched)

    # 4. Branch counts for multi-branch scoring signal
    name_counts: dict[str, int] = {}
    for r in unique:
        key = (r.get("name", "") or "").strip().lower()
        name_counts[key] = name_counts.get(key, 0) + 1

    # 5. Score
    for r in unique:
        bc = name_counts.get((r.get("name", "") or "").strip().lower(), 1)
        r.update(score_row(r, branch_count=bc))

    # 5b. Legit filter — drop ghost/low-review listings (auditable, not silent).
    legit, low_review_excluded = [], []
    for r in unique:
        if (r.get("review_count") or 0) < min_reviews:
            rr = dict(r)
            rr["exclude_reason"] = f"below minimum reviews ({min_reviews})"
            low_review_excluded.append(rr)
        else:
            legit.append(r)
    unique = legit

    # 6. Dedup against customer list
    customer_df = _load_customer_df(customer_file)
    kept, customer_excluded = dedup_mod.dedup_customers(unique, customer_df)

    # 7. Ledger filtering
    if use_ledger:
        new_leads, ledger_seen = dedup_mod.filter_against_ledger(kept)
    else:
        new_leads, ledger_seen = kept, []

    excluded = internal_dropped + low_review_excluded + customer_excluded + ledger_seen

    # 8. (No ledger write here.) A lead is committed to the ledger only when it's
    # actually released to the Sheet (crm/tracker.release_next_batch). That way the
    # ledger == "leads saved to the CRM", so scraping without releasing never burns
    # a lead, and the no-double guarantee tracks the Sheet, not transient scrapes.

    # 9. Build summary
    phone_hit = sum(1 for r in new_leads if r.get("phone_normalized"))
    summary = {
        "run_kota": kota,
        "categories": ", ".join(buckets),
        "raw_listings": len(raw),
        "after_internal_dedup": len(unique) + len(low_review_excluded),
        "min_reviews": min_reviews,
        "excluded_low_review": len(low_review_excluded),
        "excluded_customers": len(customer_excluded),
        "excluded_ledger": len(ledger_seen),
        "final_leads": len(new_leads),
        "phone_hit_rate_pct": round(100 * phone_hit / len(new_leads), 1) if new_leads else 0,
    }

    return {"leads": new_leads, "excluded": excluded, "summary": summary}


# ---------------------------------------------------------------- run
if run:
    if not buckets:
        st.error("Pick at least one industry category.")
    else:
        with st.spinner("Running…"):
            result = _run_pipeline()
        if result:
            st.session_state["result"] = result
            st.session_state["area_label"] = (
                ", ".join(selected_kecamatan) + " " + kota if (use_kecamatan and selected_kecamatan) else kota
            )

# ---------------------------------------------------------------- results
if "result" in st.session_state:
    result = st.session_state["result"]
    leads, excluded, summary = result["leads"], result["excluded"], result["summary"]

    st.subheader("Run summary")
    c = st.columns(6)
    c[0].metric("Final leads", summary["final_leads"])
    c[1].metric("Phone hit-rate", f"{summary['phone_hit_rate_pct']}%")
    c[2].metric("Raw scraped", summary["raw_listings"])
    c[3].metric(f"Excl. <{summary.get('min_reviews', 0)} rev", summary.get("excluded_low_review", 0))
    c[4].metric("Excl. customers", summary["excluded_customers"])
    c[5].metric("Excl. ledger", summary["excluded_ledger"])

    st.subheader("Leads")
    if leads:
        leads_df = pd.DataFrame(leads)[
            [c for c in exporter.LEAD_COLUMNS if c in pd.DataFrame(leads).columns]
        ].sort_values("store_size_score", ascending=False)
        st.dataframe(leads_df, use_container_width=True, height=420)
    else:
        st.info("No new leads after dedup.")

    # Export
    area_label = st.session_state.get("area_label", summary["run_kota"])
    paths = exporter.export(
        leads, excluded, summary,
        area=area_label, category=summary["categories"],
    )
    dl1, dl2 = st.columns(2)
    with open(paths["xlsx_path"], "rb") as f:
        dl1.download_button(
            "⬇ Download Excel (.xlsx)", f, file_name=paths["xlsx_path"].split("/")[-1].split("\\")[-1],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with open(paths["csv_path"], "rb") as f:
        dl2.download_button(
            "⬇ Download CSV", f, file_name=paths["csv_path"].split("/")[-1].split("\\")[-1],
            mime="text/csv", use_container_width=True,
        )
    st.caption(f"Saved to `{paths['xlsx_path']}`")

    # Excluded audit
    if excluded:
        with st.expander(f"Excluded / dedup rows ({len(excluded)}) — audit"):
            st.dataframe(pd.DataFrame(excluded), use_container_width=True)

    # ---- Sales' batch (the quality gate) ----
    st.subheader("Sales' next batch")
    st.caption(
        f"Releases the top {crm_tracker.DEFAULT_BATCH_SIZE} highest-scored fresh leads to "
        "the Google Sheet — but only once the current batch is fully worked. "
        "Quality is enforced by the gate, not requested."
    )
    st.write(gsheets.status_message())

    if not gsheets.is_configured():
        st.info("Add GSHEETS_* keys to `config/.env` (see README) to enable batch release.")
    else:
        # Show current batch completion status.
        try:
            tracker_df = gsheets.read_tracker(
                crm_tracker.TRACKER_COLUMNS, crm_tracker.TRACKER_HEADER_ROW
            )
            complete, unworked = crm_tracker.current_batch_complete(tracker_df)
            latest = crm_tracker._latest_batch_id(tracker_df)
            if latest:
                worked = len(tracker_df[
                    pd.to_numeric(tracker_df["batch_id"], errors="coerce") == latest
                ]) - len(unworked)
                total = worked + len(unworked)
                st.write(f"**Batch #{latest}:** {worked}/{total} worked"
                         + (" ✅ ready for next" if complete else " — finish the rest first"))
                if unworked:
                    st.dataframe(
                        pd.DataFrame(unworked)[["name", "status", "phone_normalized"]],
                        use_container_width=True,
                    )
        except Exception as e:
            st.warning(f"Could not read the Sheet: {e}")

        if st.button(f"📋 Build Sales' next batch ({crm_tracker.DEFAULT_BATCH_SIZE})",
                     type="primary", disabled=not leads):
            res = crm_tracker.release_next_batch(leads)
            if res["released"]:
                st.session_state["last_batch"] = res["leads"]
                st.success(res["reason"] + f" Pool left: {res['remaining_pool']}.")
                st.dataframe(
                    pd.DataFrame(res["leads"])[
                        ["name", "store_size", "store_size_score", "phone_normalized",
                         "wa_link", "maps_link", "kelurahan"]
                    ],
                    use_container_width=True,
                )
            else:
                st.error(res["reason"])
                if res["unworked"]:
                    st.dataframe(
                        pd.DataFrame(res["unworked"])[["name", "status", "phone_normalized"]],
                        use_container_width=True,
                    )

    # Qontak push (Phase 3, graduate the Sheet pipeline into Qontak)
    st.subheader("Push to Qontak")
    last_batch = st.session_state.get("last_batch", [])
    st.caption(
        "Pushes ONLY the most recently released batch (the disciplined 10), never the "
        "full pool, so the same gate that protects the Sheet protects Qontak. Without "
        "credentials this is a dry-run that logs the exact payloads to the console."
    )
    if not last_batch:
        st.info("No released batch yet. Build a batch above first, then push that batch here.")
    elif st.button(f"📤 Push released batch ({len(last_batch)}) to Qontak"):
        res = qontak.push_contacts(last_batch)
        if res["mode"] == "dry-run":
            st.info(f"Dry-run: logged {res['logged']} payloads (no credentials). "
                    "Check the terminal for the JSON payloads.")
        else:
            st.success(f"Live push: {res['sent']} sent, {res['failed']} failed.")
        for m in res["messages"]:
            st.write("•", m)

theme.footer()
