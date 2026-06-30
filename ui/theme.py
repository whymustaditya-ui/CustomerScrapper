"""Visual theme for the ROSH scraper UI — brand styling + hero header.

All look-and-feel lives here so app.py stays focused on the lead pipeline.
Streamlit's native theming is limited, so we inject scoped CSS once and render
the hero / banners / footer as small static HTML fragments.

Brand: emerald-teal "fresh, food-grade packaging" with a warm amber accent.
"""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root{
  --rosh-green:#12B886;
  --rosh-green-d:#0E9F73;
  --rosh-ink:#0F172A;
  --rosh-muted:#5B6B7B;
  --rosh-bg:#F6F8FB;
  --rosh-card:#FFFFFF;
  --rosh-border:#E6EBF1;
  --rosh-amber:#F59E0B;
}

html, body, [class*="css"], .stApp, [data-testid="stSidebar"]{
  font-family:'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.stApp{
  background:
    radial-gradient(1100px 460px at 8% -6%, #E6FBF2 0%, rgba(230,251,242,0) 55%),
    var(--rosh-bg);
}

/* product feel — drop the Deploy button, keep header out of the way */
[data-testid="stAppDeployButton"]{ display:none !important; }
[data-testid="stHeader"]{ background:transparent; }
[data-testid="stToolbar"]{ right:1rem; }

/* breathing room + sane max width */
[data-testid="stMainBlockContainer"], .block-container{
  padding-top:2.2rem; padding-bottom:3rem; max-width:1180px;
}

/* ----------------------------------------------------------- hero */
.rosh-hero{
  position:relative; overflow:hidden; border-radius:22px;
  padding:34px 38px; margin:2px 0 22px;
  background:linear-gradient(125deg,#064E3B 0%,#0B6E55 48%,#0F766E 100%);
  box-shadow:0 20px 44px -24px rgba(6,78,59,.7);
}
.rosh-hero::after{
  content:""; position:absolute; right:-70px; top:-70px;
  width:280px; height:280px; border-radius:50%;
  background:radial-gradient(circle, rgba(245,158,11,.38), rgba(245,158,11,0) 70%);
}
.rosh-badge{
  display:inline-flex; align-items:center; gap:8px;
  font-size:12px; font-weight:700; letter-spacing:.10em; text-transform:uppercase;
  color:#A7F3D0; background:rgba(255,255,255,.10);
  border:1px solid rgba(167,243,208,.32); padding:6px 13px; border-radius:999px;
}
.rosh-hero h1{
  color:#fff; font-weight:800; font-size:40px; line-height:1.04;
  margin:16px 0 8px; letter-spacing:-.02em;
}
.rosh-hero p{
  color:#CFF3E6; font-size:16px; line-height:1.5; max-width:660px; margin:0;
  position:relative; z-index:1;
}
.rosh-flow{ display:flex; flex-wrap:wrap; gap:10px; margin-top:22px; position:relative; z-index:1; }
.rosh-flow span{
  font-size:13px; font-weight:600; color:#EAFBF4;
  background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.18);
  padding:7px 14px; border-radius:10px;
}
.rosh-flow span b{ color:#6EE7B7; font-weight:700; }

/* ----------------------------------------------------------- metric cards */
[data-testid="stMetric"]{
  background:var(--rosh-card); border:1px solid var(--rosh-border);
  border-radius:16px; padding:16px 18px;
  box-shadow:0 6px 16px -12px rgba(15,23,42,.30);
}
[data-testid="stMetricValue"]{ font-weight:800; color:var(--rosh-ink); }
[data-testid="stMetricLabel"]{ color:var(--rosh-muted); font-weight:600; }

/* ----------------------------------------------------------- buttons */
.stButton>button, .stDownloadButton>button{
  border-radius:11px; font-weight:600; border:1px solid var(--rosh-border);
  transition:transform .12s ease, filter .12s ease, box-shadow .12s ease;
}
.stButton>button:hover, .stDownloadButton>button:hover{ transform:translateY(-1px); }
.stButton>button[kind="primary"]{
  background:linear-gradient(180deg,#14C28E,#0E9F73); border:none; color:#fff;
  box-shadow:0 10px 20px -12px rgba(14,159,115,.85);
}
.stButton>button[kind="primary"]:hover{ filter:brightness(1.06); }

/* ----------------------------------------------------------- sidebar */
[data-testid="stSidebar"]{ background:#FBFCFE; border-right:1px solid var(--rosh-border); }
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{
  color:var(--rosh-ink); font-weight:700; letter-spacing:-.01em;
}

/* ----------------------------------------------------------- section titles */
h2, h3{ letter-spacing:-.01em; color:var(--rosh-ink); }

/* ----------------------------------------------------------- network callout */
.rosh-callout{
  display:flex; gap:12px; align-items:flex-start;
  background:#FFF8EC; border:1px solid #FCE3B4; border-left:4px solid var(--rosh-amber);
  border-radius:12px; padding:14px 16px; margin:2px 0 10px;
}
.rosh-callout .ic{ font-size:18px; line-height:1.45; }
.rosh-callout .tx{ color:#7C5306; font-size:14px; line-height:1.5; }
.rosh-callout .tx b{ color:#663F02; }

/* ----------------------------------------------------------- dataframe */
[data-testid="stDataFrame"]{
  border-radius:12px; overflow:hidden; border:1px solid var(--rosh-border);
}

/* ----------------------------------------------------------- footer */
.rosh-footer{
  margin-top:34px; padding-top:16px; border-top:1px solid var(--rosh-border);
  color:var(--rosh-muted); font-size:13px; text-align:center;
}
.rosh-footer b{ color:var(--rosh-green-d); font-weight:700; }
</style>
"""

_HERO = """
<div class="rosh-hero">
  <span class="rosh-badge">📦 ROSH · B2B Packaging Distribution</span>
  <h1>Super Customer Scraper</h1>
  <p>Lead engine for Thinwall &amp; Cup Oz. Find F&amp;B buyers on Google Maps,
     score them by store size, and route only the best into your CRM.</p>
  <div class="rosh-flow">
    <span>① <b>Scrape</b> Maps</span>
    <span>② <b>Score</b> &amp; filter</span>
    <span>③ <b>Dedup</b> vs ledger</span>
    <span>④ <b>Release</b> to CRM</span>
  </div>
</div>
"""

_NETWORK_WARNING = """
<div class="rosh-callout">
  <div class="ic">⚠️</div>
  <div class="tx"><b>Do not run on MoF / DJP network.</b> GMaps scraping breaches
     Google ToS and risks IP blocks. Run from a personal or business connection
     only, and keep volume modest.</div>
</div>
"""

_FOOTER = """
<div class="rosh-footer">
  <b>ROSH Finance</b> · internal lead tooling · Thinwall &amp; Cup Oz distribution
</div>
"""


def apply_theme() -> None:
    """Inject the brand CSS. Call once, right after st.set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)


def hero() -> None:
    """Render the gradient brand header."""
    st.markdown(_HERO, unsafe_allow_html=True)


def network_warning() -> None:
    """Render the DJP-network safety callout."""
    st.markdown(_NETWORK_WARNING, unsafe_allow_html=True)


def footer() -> None:
    """Render the muted brand footer."""
    st.markdown(_FOOTER, unsafe_allow_html=True)
