"""
WattleFolio Shariah Checker: screens shares and ETFs for Shariah compliance.
Screening rules follow the WattleFolio methodology (tiers at 30% / 33%, 5% revenue limit,
24-month average market cap, 60-day exit window, purging, 2.5% zakat).
"""
import base64
import csv
import html
import io
import math
import os
import urllib.request
import re
import time
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
from holdings import parse_holdings_file, yahoo_candidates
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

# ----------------------------------------------------------------------------- settings
TIER1_MAX = 0.30          # Tier 1: highest ratio <= 30%
TIER2_MAX = 0.33          # Tier 2: 30.1% - 33%; above = Tier 3
REVENUE_LIMIT = 0.05      # non-permissible revenue must be below 5%
PRE_REVENUE_SHARE = 0.5   # interest above this share of revenue = no real sales yet (explorers, biotechs): manual check, not auto-fail
STALE_DAYS = 450          # warn when the latest company figures are older than this
EXIT_DAYS = 60            # orderly liquidation window for Tier 3
ZAKAT_RATE = 0.025        # 2.5% of Tier 1 & 2 market value
# Other standards, shown for comparison only (they don't change the tier)
AAOIFI_LIMIT = 0.30       # debt and cash, each vs current market cap
DJIM_LIMIT = 0.33         # Dow Jones Islamic: vs 24-month average market cap
DJIM_TEST_CASH_RECEIVABLES = True   # DJIM reportedly dropped these two tests in Sept 2023; set False once confirmed
SP_LIMIT = 0.33           # S&P Shariah: debt and cash vs 36-month average market cap
SP_RECEIVABLES_LIMIT = 0.49   # S&P Shariah: receivables + cash vs 36-month average market cap
MSCI_LIMIT = 0.3333       # MSCI Islamic: debt, cash, receivables + cash, each vs total assets
INCLUDE_LEASES = True     # count lease liabilities as interest-bearing debt
ISLAMIC_FUND_WORDS = ["islamic", "shariah", "sharia", "syariah", "halal"]   # in a fund's name = has its own Shariah board
FUND_FULL_COVERAGE = 0.99  # share of an ETF the published holdings must cover to give a full verdict
FUND_MAX_BONDS = 0.01     # conventional bonds pay interest; more than this fails an ETF
# Full ETF holdings lists (uploaded, saved in etf_holdings/, or downloaded from SPDR): check the largest holdings
# until this much of the fund is covered, up to a maximum number. A full list that reaches the target can pass.
ETF_FULL_TARGET = 0.95
ETF_FULL_MAX = 150
HOLDINGS_DIR = "etf_holdings"
try:
    APP_TZ = ZoneInfo("Australia/Sydney")   # times shown in the app (e.g. when a check was done)
except ZoneInfoNotFoundError:            # server without time-zone data (tzdata is in requirements.txt)
    APP_TZ = ZoneInfo("UTC")
PROFILE_RETRY_MINUTES = 15   # keep a result with a blank Yahoo profile this long before asking Yahoo again
ETF_DEPTHS = {"quick": "Quick: top holdings from Yahoo",
              "full": "Full: every holding from the fund provider (slower)"}
SPDR_HOLDINGS_URL = "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{}.xlsx"
BETASHARES_HOLDINGS_URL = "https://www.betashares.com.au/files/csv/{}_Portfolio_Holdings.csv"
# "Find stocks" tab: the largest companies in a market are screened, then the best compliant ones listed
IDEAS_MARKETS = {"Australia (ASX)": "au", "United States": "us", "Malaysia (Bursa)": "my"}
IDEAS_CANDIDATES = 100    # how many of the largest companies to screen (more = slower first load)
IDEAS_SHOW = 50
IDEAS_SKIP_SECTORS = ["Financial Services"]   # mostly banks and insurers, so not worth screening
IDEAS_ETF_CANDIDATES = 60   # largest ETFs per market to look through (each one checks ~10 holdings)
# "Show prices in" choices; the choice is kept in the page link (?ccy=AUD)
DISPLAY_CURRENCIES = ["AUD", "USD", "SGD", "MYR", "GBP", "EUR", "NZD", "HKD", "CAD", "JPY", "IDR"]
MARKET_SUFFIX = {"au": ".AX", "my": ".KL", "us": ""}   # Yahoo code endings, to keep name-search hits in the market
NEXT_REVIEW = date(2027, 2, 7)   # last day of Sha'ban 1448 (approx, confirm by moon sighting)
MANUAL_CHECK_TIP = ("Before buying, look at the company's latest annual report: check what it earns its revenue "
                    "from and whether any of it is non-permissible, or ask a scholar you trust.")

# Automatic business check (sector_review.csv always overrides it)
# Industries that fail automatically
EXCLUDED_KEYWORDS = ["bank", "insurance", "credit services", "mortgage", "capital markets",
                     "financial conglomerate", "asset management", "gambling", "casino",
                     "brewer", "winer", "distiller", "tobacco"]
# Industries that often mix permissible and non-permissible sales, so always need a manual review
REVIEW_KEYWORDS = ["aerospace & defense", "restaurant", "lodging", "entertainment", "resorts", "leisure",
                   "packaged foods", "farm products", "food distribution", "grocery", "discount stores",
                   "department stores", "reit", "conglomerates", "shell companies", "broadcasting",
                   "travel services", "electronic gaming"]
# Words in the company's description that need a manual review (whole words, any case; * = any ending)
DESCRIPTION_FLAGS = ["alcohol*", "liquor*", "beer*", "wine*", "spirits", "brew*", "distill*",
                     "tobacco", "cigar*", "vap*", "e-cigarette*", "gambling", "casino*", "wager*", "betting",
                     "lotter*", "poker", "pork", "swine", "ham", "bacon", "adult entertainment", "pornograph*",
                     "nightclub*", "lending", "loans", "mortgage*", "consumer finance", "insurance", "banking",
                     "weapon*", "firearm*", "ammunition", "munitions"]

MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}

# WattleFolio design tokens for the parts Streamlit's theme doesn't colour (verdicts, badges, cards, bars)
PALETTES = {
    "light": {"ink": "#2d4039", "muted": "#5f6e68", "primary": "#35645a", "accent": "#f4ddae", "surface": "#ffffff",
              "line": "#dfe8e1", "bar": "#e2f1ed", "good": "#15805d", "watch": "#dfbd78", "bad": "#c2413a",
              "note-bg": "#fffbed", "note-line": "#dfbd78", "edge": "rgba(45,64,57,0.08)",
              "shadow": "0 1px 2px rgba(38,70,61,0.035), 0 12px 30px rgba(56,87,77,0.07)",
              "t1-bg": "#ecfdf5", "t1-fg": "#126247", "t2-bg": "#fff9e8", "t2-fg": "#8b5b12",
              "t3-bg": "#fff1f0", "t3-fg": "#a8352f", "t0-bg": "#f8f7f5", "t0-fg": "#5f6e68"},
    "dark": {"ink": "#eae8df", "muted": "#a7b1ac", "primary": "#99c2b7", "accent": "#c39f52", "surface": "#161e1b",
             "line": "#2b3431", "bar": "#1d2e2a", "good": "#75cda3", "watch": "#c39f52", "bad": "#ef8a82",
             "note-bg": "#352a11", "note-line": "#6a5526", "edge": "rgba(255,255,255,0.06)",
             "shadow": "0 1px 2px rgba(0,0,0,0.36), 0 12px 30px rgba(0,0,0,0.34)",
             "t1-bg": "#0e3222", "t1-fg": "#77d4a8", "t2-bg": "#302816", "t2-fg": "#edc56e",
             "t3-bg": "#44211e", "t3-fg": "#ef8a82", "t0-bg": "#1e2623", "t0-fg": "#a7b1ac"},
}


def theme_mode():
    """"light" or "dark" as Streamlit is showing it, or None if it can't tell (then the device setting decides)."""
    try:
        mode = st.context.theme.type
    except Exception:
        return None
    return mode if mode in PALETTES else None


def theme_css():
    def block(mode):
        return ":root{" + ";".join(f"--wf-{k}:{v}" for k, v in PALETTES[mode].items()) + "}"
    mode = theme_mode()
    if mode:
        return block(mode)
    return block("light") + "@media (prefers-color-scheme: dark){" + block("dark") + "}"


TIER_STYLE = {
    "Tier 1": ("Compliant", "var(--wf-t1-bg)", "var(--wf-t1-fg)", "Fine to hold and to buy more."),
    "Tier 2": ("On watch", "var(--wf-t2-bg)", "var(--wf-t2-fg)", "Keep the shares you have, but don't buy more for now."),
    "Tier 3": ("Not compliant", "var(--wf-t3-bg)", "var(--wf-t3-fg)", f"Don't buy. Sell existing shares within {EXIT_DAYS} days."),
    "Incomplete": ("Can't tell yet", "var(--wf-t0-bg)", "var(--wf-t0-fg)", "Some company figures are missing, so this stock can't be scored."),
}

st.set_page_config(page_title="WattleFolio Shariah Checker", page_icon="assets/wattlefolio-mark.png", layout="centered")
st.markdown(f"<style>{theme_css()}</style>", unsafe_allow_html=True)
st.markdown("""
<style>
html, body, [class*="css"] { font-size: 18px; }
html, body, .stApp, .stApp p, .stApp li, .stApp input, .stApp button, .stApp label,
.stApp h1, .stApp h2, .stApp h3, .stApp [data-testid="stMarkdownContainer"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, system-ui, sans-serif; }
.stApp h1, .stApp h2, .stApp h3 { color: var(--wf-ink); letter-spacing: -0.01em; }
.brand { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; margin: 0 0 0.6rem; }
.brand img { height: 40px; width: auto; }
.brand span { font-size: 1.05rem; font-weight: 600; color: var(--wf-primary); border-left: 2px solid var(--wf-accent); padding-left: 0.75rem; }
.verdict { border-radius: 8px; padding: 1.4rem 1.5rem; margin: 0.8rem 0 1.2rem; border: 1px solid var(--wf-edge);
           box-shadow: var(--wf-shadow); }
.verdict .label { font-size: 2.3rem; font-weight: 750; line-height: 1.1; letter-spacing: -0.01em; }
.verdict .action { font-size: 1.2rem; margin-top: 0.4rem; }
.verdict .who { font-size: 0.95rem; margin-top: 0.9rem; opacity: 0.8; }
.note { border-left: 4px solid var(--wf-note-line); background: var(--wf-note-bg); padding: 0.5rem 0.9rem; margin: 0.4rem 0 1rem; }
.bar { position: relative; height: 12px; border-radius: 6px; background: var(--wf-bar); margin: 0.25rem 0 0.9rem; }
.bar .fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; }
.bar .tick { position: absolute; top: -4px; bottom: -4px; width: 2px; background: var(--wf-muted); }
.row { display: flex; justify-content: space-between; font-size: 1rem; }
.src { font-size: 0.85rem; opacity: 0.8; margin: -0.6rem 0 1rem; line-height: 1.5; }
.stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.5rem; margin: 0 0 0.6rem; }
.stat .v .txt { font-size: 0.95rem; }
.stat { background: var(--wf-surface); border: 1px solid var(--wf-line); border-radius: 8px; padding: 0.55rem 0.8rem;
        min-width: 0; overflow-wrap: anywhere; }
.stat .k { font-size: 0.78rem; color: var(--wf-muted); }
.stat .v { font-size: 1.15rem; font-weight: 700; margin-top: 0.1rem; line-height: 1.3; }
.stat .v .nw { white-space: nowrap; }
.stat .s { font-size: 0.75rem; color: var(--wf-muted); margin-top: 0.1rem; }
.up { color: var(--wf-good); font-weight: 600; }
.down { color: var(--wf-bad); font-weight: 600; }
.wsum { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.4rem 0 0.8rem; }
.wcard { background: var(--wf-surface); border: 1px solid var(--wf-line); border-radius: 8px; padding: 0.75rem 0.9rem;
         margin: 0.8rem 0 0.4rem; }
.wtop { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.4rem 0.6rem; flex-wrap: wrap; }
.wtop .pill { white-space: normal; text-align: left; }
/* button pairs (Open / Remove, Open / Watch) stay side by side on phones */
[class*="st-key-pair_"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 0.5rem; }
[class*="st-key-pair_"] [data-testid="stColumn"] { min-width: 0 !important; width: auto !important; flex: 1 1 0 !important; }
.wtop small { color: var(--wf-muted); margin-left: 0.2rem; }
.wprice { font-size: 1.05rem; font-weight: 700; margin: 0.25rem 0 0.2rem; }
.wsub { font-size: 0.82rem; color: var(--wf-muted); line-height: 1.45; }
.wsub b { color: var(--wf-ink); font-weight: 600; }
.links { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: -0.4rem 0 1.1rem; }
.links a { display: inline-block; padding: 0.3rem 0.75rem; border: 1px solid var(--wf-line); border-radius: 999px;
           background: var(--wf-surface); color: var(--wf-primary) !important; font-size: 0.85rem; font-weight: 600;
           text-decoration: none !important; white-space: nowrap; }
.links a:hover { border-color: var(--wf-primary); }
.mix { margin: 0.4rem 0 1rem; }
.mixbar { display: flex; height: 14px; border-radius: 7px; overflow: hidden; background: var(--wf-bar); margin-bottom: 0.5rem; }
.mix .line { display: flex; justify-content: space-between; gap: 0.75rem; font-size: 0.95rem; padding: 0.15rem 0; }
.mix .line span:last-child { white-space: nowrap; }
.mix small { opacity: 0.7; }
.dot { display: inline-block; width: 0.7rem; height: 0.7rem; border-radius: 50%; margin-right: 0.45rem; vertical-align: baseline;
       box-shadow: inset 0 0 0 1px var(--wf-line); }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 0.75rem; margin: 0.5rem 0 1rem; }
.card { border: 1px solid var(--wf-line); border-radius: 8px; padding: 0.8rem 1rem; background: var(--wf-surface);
        box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.card .head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; font-weight: 700; }
.card .sub { font-size: 0.85rem; opacity: 0.75; margin: 0.1rem 0 0.4rem; }
.card .line { display: flex; justify-content: space-between; gap: 0.75rem; font-size: 0.95rem; padding: 0.15rem 0; }
.card .line span:last-child { text-align: right; }
.card .line.bad span:last-child, .card .line.fail span:last-child { color: var(--wf-bad); font-weight: 700; }
.card .line.warn span:last-child, .card .line.watch span:last-child { color: var(--wf-t2-fg); font-weight: 600; }
.card .line span:last-child small { font-weight: 400; }
.card .why { color: var(--wf-ink); font-size: 0.85rem; margin-top: 0.35rem; line-height: 1.4; }
.mk { font-style: normal; display: inline-block; width: 1.1rem; font-weight: 700; flex: none; }
.card .line span:first-child { display: flex; white-space: nowrap; }
.mk.pass { color: var(--wf-good); } .mk.fail { color: var(--wf-bad); }
.mk.warn, .mk.watch { color: var(--wf-t2-fg); } .mk.na { color: var(--wf-muted); }
.card small { opacity: 0.7; }
.pill { display: inline-block; border-radius: 999px; padding: 0.1rem 0.6rem; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
.holding { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.75rem; padding: 0.55rem 0;
           border-bottom: 1px solid var(--wf-line); }
.holding .nm { font-weight: 600; overflow-wrap: anywhere; }
.holding .meta { font-size: 0.85rem; margin-top: 0.2rem; display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
.holding .wt { font-weight: 600; white-space: nowrap; }
@media (max-width: 640px) {
  .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  html, body, [class*="css"] { font-size: 16px; }
  .verdict { padding: 1.1rem 1.2rem; }
  .verdict .label { font-size: 1.8rem; }
  .brand { flex-direction: column; align-items: flex-start; gap: 0.25rem; }
  .brand span { border-left: none; padding-left: 0; }
  .verdict .action { font-size: 1.05rem; }
}
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- data
@st.cache_data(ttl=60)
def load_review():
    try:
        df = pd.read_csv("sector_review.csv", dtype=str).fillna("")
        df["ticker"] = df["ticker"].str.strip().str.upper()
        return {r["ticker"]: r for _, r in df.iterrows()}
    except Exception:
        return {}


@st.cache_data(ttl=3600)
def fx_rate(src, dst):
    if not src or not dst or src == dst:
        return 1.0
    hist = yf.Ticker(f"{src}{dst}=X").history(period="5d")
    return float(hist["Close"].dropna().iloc[-1])


def newest_first(df):
    return None if df is None or df.empty else df[sorted(df.columns, reverse=True)]


@st.cache_data(ttl=15 * 60, show_spinner=False)
def quote(symbol):
    """Latest price and previous close (in the quote's own units) and the price date. Refreshed every 15 minutes."""
    closes = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)["Close"].dropna()
    prev = float(closes.iloc[-2]) if len(closes) > 1 else None
    return float(closes.iloc[-1]), prev, closes.index[-1].strftime("%d %b %Y")


def days_old(ts):
    return (pd.Timestamp.now() - pd.Timestamp(ts).tz_localize(None)).days


def dividends_12m(t, divisor):
    """Dividends paid per share over the last 12 months (Yahoo, quote currency). None if unavailable."""
    try:
        divs = t.dividends
        if divs is None or divs.empty:
            return 0.0
        cutoff = pd.Timestamp.now(tz=divs.index.tz) - pd.Timedelta(days=365)
        return float(divs[divs.index >= cutoff].sum()) / divisor
    except Exception:
        return None


def fetch_fund(t, info, symbol):
    """ETF or managed fund: price, dividends and the top holdings Yahoo publishes."""
    quote_ccy = info.get("currency")
    divisor = 1
    if quote_ccy in MINOR_UNITS:
        quote_ccy, divisor = MINOR_UNITS[quote_ccy]
    closes = t.history(period="2y", interval="1mo", auto_adjust=False)["Close"].dropna()
    if closes.empty:
        raise ValueError("no price data found")
    holdings, assets, sectors, fee, family = [], {}, {}, None, None
    try:
        fd = t.funds_data
        top = fd.top_holdings
        if top is not None and not top.empty:
            holdings = [(str(sym), str(row["Name"]), float(row["Holding Percent"])) for sym, row in top.iterrows()]
        assets = fd.asset_classes or {}
        sectors = fd.sector_weightings or {}
        family = (fd.fund_overview or {}).get("family")
        ops = fd.fund_operations
        if ops is not None and "Annual Report Expense Ratio" in ops.index:
            raw = pd.to_numeric(ops.loc["Annual Report Expense Ratio"].iloc[0], errors="coerce")
            fee = None if pd.isna(raw) else float(raw) / (100 if raw > 0.2 else 1)   # some funds report it as a %
    except Exception:
        pass
    return {
        "kind": "fund",
        "checked_at": datetime.now(APP_TZ).strftime("%d %b %Y, %H:%M %Z"),
        "website": info.get("website") or "",
        "exchange_name": info.get("fullExchangeName") or info.get("exchange") or "",
        "pe": info.get("trailingPE"),
        "market_cap_quote": info.get("marketCap"),
        "low_52w": (info.get("fiftyTwoWeekLow") or 0) / divisor or None,
        "high_52w": (info.get("fiftyTwoWeekHigh") or 0) / divisor or None,
        "family": family or info.get("fundFamily") or "",
        "fee": fee,
        "size": info.get("totalAssets") or info.get("netAssets"),
        "ret_1y": float(closes.iloc[-1] / closes.iloc[-13] - 1) if len(closes) >= 13 else None,
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "exchange": info.get("exchange", ""),
        "price": float(closes.iloc[-1]) / divisor,
        "price_date": f"end of {closes.index[-1]:%b %Y}",
        "price_ccy": quote_ccy,
        "divisor": divisor,
        "holdings": holdings,
        "bonds": assets.get("bondPosition") or 0.0,
        "stocks": assets.get("stockPosition"),
        "sectors": {k: v for k, v in sectors.items() if v},
        "dps_12m": dividends_12m(t, divisor),
    }


def company_info(symbol):
    """Yahoo's company profile, retried: Yahoo sometimes sends cloud servers a blank one. (info, complete?)"""
    info = {}
    for attempt in range(2):   # one quick retry: more only adds load when Yahoo is limiting the server
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:
            info = {}
        if info.get("quoteType") and info.get("currency"):
            return info, True
        if attempt == 0:
            time.sleep(0.5)
    return info, False


def _cell(df, col, *labels):
    for label in labels:
        if label in df.index:
            v = df.at[label, col]
            if pd.notna(v):
                return float(v)
    return None


def balance_sheet_figures(df):
    """Debt, cash, money owed and total assets, all from ONE balance sheet date: the newest that reports them.

    Falls back to the newest sheet with debt, cash and total assets when the company never reports money owed
    (then "receivables" is None)."""
    df = newest_first(df)
    if df is None:
        return None
    without_receivables = None
    for col in df.columns:
        assets = _cell(df, col, "Total Assets")
        total_debt = _cell(df, col, "Total Debt")
        current, long_term = _cell(df, col, "Current Debt"), _cell(df, col, "Long Term Debt")
        cash_all = _cell(df, col, "Cash Cash Equivalents And Short Term Investments")
        cash_only = _cell(df, col, "Cash And Cash Equivalents")
        if assets is None or (total_debt is None and current is None and long_term is None) or \
                (cash_all is None and cash_only is None):
            continue
        leases = _cell(df, col, "Capital Lease Obligations") or 0
        if total_debt is not None:   # "Total Debt" includes leases
            debt = total_debt - (0 if INCLUDE_LEASES else leases)
        else:                        # "Current Debt" and "Long Term Debt" exclude leases
            debt = (current or 0) + (long_term or 0) + (leases if INCLUDE_LEASES else 0)
        cash = cash_all if cash_all is not None else cash_only + (_cell(df, col, "Other Short Term Investments") or 0)
        figures = {"date": col, "debt": debt, "cash": cash, "assets": assets,
                   "receivables": _cell(df, col, "Accounts Receivable", "Receivables"),
                   "shares": _cell(df, col, "Ordinary Shares Number", "Share Issued")}
        if figures["receivables"] is not None:
            return figures
        without_receivables = without_receivables or figures
    return without_receivables


def income_figures(df):
    """Revenue and interest income from the same year: the newest annual report that has revenue."""
    df = newest_first(df)
    if df is None:
        return None
    for col in df.columns:
        revenue = _cell(df, col, "Total Revenue", "Operating Revenue")
        if revenue:
            return {"date": col, "revenue": revenue,
                    "interest_income": _cell(df, col, "Interest Income", "Interest Income Non Operating")}
    return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch(symbol):
    info, profile_ok = company_info(symbol)
    t = yf.Ticker(symbol)
    if not profile_ok:   # fill the basics from Yahoo's price data instead
        try:
            t.history(period="5d")
            md = t.get_history_metadata() or {}
        except Exception:
            md = {}
        basics = {"quoteType": md.get("instrumentType"), "currency": md.get("currency"),
                  "longName": md.get("longName"), "shortName": md.get("shortName"),
                  "fullExchangeName": md.get("fullExchangeName"), "exchange": md.get("exchangeName")}
        info = {**{k: v for k, v in basics.items() if v}, **{k: v for k, v in info.items() if v}}
    if info.get("quoteType") in ("ETF", "MUTUALFUND"):
        return fetch_fund(t, info, symbol)

    # Balance sheet: whichever is newer, the annual report or the latest interim (half-year/quarterly) one,
    # with every figure taken from that same date
    sheets = [(f["date"], kind, f) for kind, f in (("annual", balance_sheet_figures(t.balance_sheet)),
                                                   ("interim", balance_sheet_figures(t.quarterly_balance_sheet))) if f]
    if not sheets:
        raise ValueError("no complete balance sheet found")
    _, bs_kind, bs = max(sheets, key=lambda c: c[0])   # a tie keeps the annual report
    inc = income_figures(t.income_stmt) or {}

    quote_ccy = info.get("currency")
    fin_ccy = info.get("financialCurrency") or quote_ccy
    fin_ccy_assumed = not info.get("financialCurrency")
    divisor = 1
    if quote_ccy in MINOR_UNITS:
        quote_ccy, divisor = MINOR_UNITS[quote_ccy]
    conv = fx_rate(quote_ccy, fin_ccy) / divisor

    shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
    if not shares:
        try:
            shares = t.fast_info.get("shares")
        except Exception:
            shares = None
    if not shares:
        shares = bs["shares"]
    closes = t.history(period="3y", interval="1mo", auto_adjust=False)["Close"].dropna()
    if not shares or closes.empty:
        raise ValueError("no share price data found")

    return {
        "kind": "stock",
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "exchange": info.get("exchange", ""),
        "industry": info.get("industry") or "",
        "sector": info.get("sector") or "",
        "summary": info.get("longBusinessSummary") or "",
        "website": info.get("website") or "",
        "exchange_name": info.get("fullExchangeName") or info.get("exchange") or "",
        "pe": info.get("trailingPE"),
        "market_cap_quote": info.get("marketCap"),
        "low_52w": (info.get("fiftyTwoWeekLow") or 0) / divisor or None,
        "high_52w": (info.get("fiftyTwoWeekHigh") or 0) / divisor or None,
        "price": float(closes.iloc[-1]) / divisor,
        "price_date": f"end of {closes.index[-1]:%b %Y}",
        "price_ccy": quote_ccy,
        "fin_ccy": fin_ccy,
        "fin_ccy_assumed": fin_ccy_assumed,
        "profile_missing": not profile_ok,
        "divisor": divisor, "conv": conv, "shares": float(shares),
        "revenue": inc.get("revenue"),
        "interest_income": inc.get("interest_income"),
        "interest_missing": bool(inc) and inc.get("interest_income") is None,
        "debt": bs["debt"],
        "cash": bs["cash"],
        "receivables": bs["receivables"] or 0.0,
        "receivables_missing": bs["receivables"] is None,
        "assets": bs["assets"],
        "avg_mcap": float(closes.iloc[-24:].mean()) * shares * conv,
        "avg_mcap_36": float(closes.iloc[-36:].mean()) * shares * conv,
        "dps_12m": dividends_12m(t, divisor),
        "ret_1y": float(closes.iloc[-1] / closes.iloc[-13] - 1) if len(closes) >= 13 else None,
        "mcap": float(closes.iloc[-1]) * shares * conv,
        "as_of": bs["date"].strftime("%d %b %Y"),
        "checked_at": datetime.now(APP_TZ).strftime("%d %b %Y, %H:%M %Z"),
        "fetched_ts": time.time(),
        "bs_kind": bs_kind,
        "bs_label": (f"{bs['date']:%d %b %Y} (FY{bs['date'].year} annual report)" if bs_kind == "annual"
                     else f"{bs['date']:%d %b %Y} (latest half-year or quarterly report)"),
        "inc_label": (f"FY{inc['date'].year} annual report (year to {inc['date']:%d %b %Y})" if inc
                      else "not available"),
        "bs_days": days_old(bs["date"]),
        "inc_days": days_old(inc["date"]) if inc else 0,
    }


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def search(query):
    try:
        quotes = yf.Search(query, max_results=8).quotes
    except Exception:
        return []
    return [(q["symbol"], f'{q.get("shortname") or q.get("longname") or q["symbol"]}  ({q["symbol"]}, {q.get("exchange", "")})')
            for q in quotes if q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND") and q.get("symbol")]


# ----------------------------------------------------------------------------- screening
def ratio(a, b):
    return a / b if a is not None and b else None


def review_pct(review):
    """Review-list non-permissible revenue %, tolerating '1.2%' or '1,2'. None if blank or unreadable."""
    raw = review["non_permissible_revenue_pct"].strip().rstrip("%").strip().replace(",", ".")
    try:
        return float(raw) / 100 if raw else None
    except ValueError:
        return None


def _pct(v):
    return "–" if v is None else f"{v:.1%}"


def _limit_test(label, value, limit):
    """A ratio test line: (label, shown value, status) with status pass / fail / na."""
    if value is None:
        return (label, "figure missing", "na")
    return (label, f"{_pct(value)} <small>/ max {limit * 100:g}%</small>", "pass" if value <= limit else "fail")


def _plain(value_html):
    t = re.sub(r"<[^>]+>", "", value_html).strip().rstrip(".")
    return t[:1].lower() + t[1:]


WARN_PHRASES = {"Business activity": "the business activity needs a manual check",
                "Income test": "the income test isn't meaningful yet, as the company has almost no sales"}


def _summary(tests):
    """One plain sentence on why a standard passes or fails, from its test lines."""
    fails = [t for t in tests if t[2] == "fail"]
    passes = [t[0].lower() for t in tests if t[2] == "pass"]
    warns = [t for t in tests if t[2] == "warn"]
    missing = [t[0].lower() for t in tests if t[2] == "na"]
    if fails:
        why = "Fails: " + "; ".join(f"{t[0].lower()} ({_plain(t[1])})" for t in fails) + "."
        return why + (f" Passes: {', '.join(passes)}." if passes else "")
    if missing:
        return f"Can't be fully checked: {', '.join(missing)} missing."
    if warns:
        return "The numbers pass, but " + " and ".join(WARN_PHRASES.get(t[0], _plain(t[1])) for t in warns) + "."
    return "Passes every test."


def standards(d, business_kind, business_note, income):
    """Each standard's tests as shown in "How other standards see it".

    business_kind: excluded / review / pass. income: {"revenue": share of revenue, "total": share of total income,
    "source": text, "na": reason or None, "pre_revenue": bool}.
    Returns {name: {"basis", "tests": [(label, value_html, status)], "result", "summary"}}."""
    cash_recv = d["cash"] + d["receivables"] if d["cash"] is not None else None

    def income_line(share_of):
        if income.get("pre_revenue"):
            return ("Income test", "Not meaningful yet: the company has almost no sales.", "warn")
        if income.get("na"):
            return ("Income test", income["na"], "na")
        v = income[share_of]
        what = "of total income" if share_of == "total" else "of revenue"
        return ("Income test", f"{_pct(v)} {what} <small>/ under 5%</small>",
                "pass" if v < REVENUE_LIMIT else "fail")

    business_line = None
    if business_kind == "excluded":
        business_line = ("Business activity", html.escape(business_note), "fail")
    elif business_kind == "review":
        business_line = ("Business activity", "Needs a manual check.", "warn")

    djim = [_limit_test("Debt", ratio(d["debt"], d["avg_mcap"]), DJIM_LIMIT)]
    if DJIM_TEST_CASH_RECEIVABLES:
        djim += [_limit_test("Cash", ratio(d["cash"], d["avg_mcap"]), DJIM_LIMIT),
                 _limit_test("Receivables", ratio(d["receivables"], d["avg_mcap"]), DJIM_LIMIT)]
    out = {
        # AAOIFI Standard 21 measures non-permissible income against the company's total income
        "AAOIFI": ("current market value", [
            _limit_test("Debt", ratio(d["debt"], d["mcap"]), AAOIFI_LIMIT),
            _limit_test("Cash", ratio(d["cash"], d["mcap"]), AAOIFI_LIMIT), income_line("total")]),
        "Dow Jones Islamic": ("2-year average market value", djim + [income_line("revenue")]),
        "S&P Shariah": ("3-year average market value", [
            _limit_test("Debt", ratio(d["debt"], d["avg_mcap_36"]), SP_LIMIT),
            _limit_test("Cash", ratio(d["cash"], d["avg_mcap_36"]), SP_LIMIT),
            _limit_test("Receivables + cash", ratio(cash_recv, d["avg_mcap_36"]), SP_RECEIVABLES_LIMIT),
            income_line("revenue")]),
        "MSCI Islamic": ("total assets", [
            _limit_test("Debt", ratio(d["debt"], d["assets"]), MSCI_LIMIT),
            _limit_test("Cash", ratio(d["cash"], d["assets"]), MSCI_LIMIT),
            _limit_test("Receivables + cash", ratio(cash_recv, d["assets"]), MSCI_LIMIT), income_line("revenue")]),
    }
    results = {}
    for name, (basis, tests) in out.items():
        if business_line:
            tests = tests + [business_line]
        statuses = {t[2] for t in tests}
        result = ("Fail" if "fail" in statuses else "n/a" if "na" in statuses
                  else "Needs a look" if "warn" in statuses else "Pass")
        results[name] = {"basis": basis, "tests": tests, "result": result, "summary": _summary(tests)}
    return results


def wattlefolio_card(d, s):
    """The WattleFolio rules laid out like the other standards, so the verdict at the top is explained."""
    tests = []
    for label, key in (("Debt", "Debt"), ("Cash", "Cash & interest-earning investments"),
                       ("Money owed", "Money owed to the company")):
        v = s["ratios"][key]
        if v is None:
            tests.append((label, "figure missing", "na"))
        else:
            status = "pass" if v <= TIER1_MAX else "watch" if v <= TIER2_MAX else "fail"
            tests.append((label, f"{_pct(v)} <small>/ {TIER1_MAX * 100:g}% (watch {TIER2_MAX * 100:g}%)</small>", status))
    inc = s["income"]
    if inc.get("pre_revenue"):
        tests.append(("Income test", "Not meaningful yet: the company has almost no sales.", "warn"))
    elif inc.get("na"):
        tests.append(("Income test", inc["na"], "na"))
    else:
        tests.append(("Income test", f"{_pct(inc['revenue'])} of revenue <small>/ under 5%</small>",
                      "pass" if inc["revenue"] < REVENUE_LIMIT else "fail"))
    if s["business_kind"] == "excluded":
        tests.append(("Business activity", html.escape(s["business_note"]), "fail"))
    elif s["business_kind"] == "review":
        tests.append(("Business activity", "Needs a manual check.", "warn"))
    elif s["business_kind"] == "pass":
        tests.append(("Business activity", "Passes" + (" (checked manually)" if s["checked_by"] == "manual" else
                                                       " (automatic check)"), "pass"))
    summary = _summary([(t[0], t[1], "warn" if t[2] == "watch" else t[2]) for t in tests])
    if any(t[2] == "watch" for t in tests) and not any(t[2] == "fail" for t in tests):
        summary = "On watch: " + ", ".join(t[0].lower() for t in tests if t[2] == "watch") + \
                  f" is between {TIER1_MAX * 100:g}% and {TIER2_MAX * 100:g}%."
    return {"basis": "2-year average market value", "tests": tests, "result": TIER_STYLE[s["tier"]][0],
            "summary": summary}


FLAG_RE = re.compile(r"\b(" + "|".join(re.escape(w).replace(r"\*", r"\w*") for w in DESCRIPTION_FLAGS) + r")\b",
                     re.IGNORECASE)


def description_flags(text):
    """Distinct flagged words found in a company description, in the order they appear."""
    return list(dict.fromkeys(m.lower() for m in FLAG_RE.findall(text or "")))


def screen(d):
    review = load_review().get(d["symbol"].upper())
    industry = f'{d["industry"]} {d["sector"]}'.lower()

    # Non-permissible revenue: review-list figure if entered, otherwise interest income as an estimate
    np_pct, np_source, np_total = None, "", None
    if review is not None and review_pct(review) is not None:
        np_pct, np_source = review_pct(review), "WattleFolio review list"
        np_total = np_pct
    elif d["revenue"]:
        interest = abs(d["interest_income"] or 0)
        np_pct, np_source = min(interest / d["revenue"], 1.0), "estimate (interest income only)"
        np_total = interest / (d["revenue"] + interest)   # AAOIFI: share of total income
        if review is not None and review["non_permissible_revenue_pct"].strip():
            np_source += "; the figure in the review list couldn't be read"

    # Almost no sales yet (explorers, biotechs): interest on cash dwarfs revenue, so the 5% test says little
    excluded_industry = review is None and any(k in industry for k in EXCLUDED_KEYWORDS)
    pre_revenue = (np_source.startswith("estimate") and np_pct is not None and np_pct >= PRE_REVENUE_SHARE
                   and not excluded_industry)   # banks earn mostly interest: that's not "no sales yet"
    checked_by = "manual" if review is not None and review["excluded"].strip().lower() in ("yes", "no") else "automatic"
    business_kind, business_note = "pass", ""
    if review is not None and review["excluded"].strip().lower() == "yes":
        business, why = "Fail", "Marked as excluded in the WattleFolio review list."
        business_kind, business_note = "excluded", "Marked as excluded in the review list"
    elif review is None and any(k in industry for k in EXCLUDED_KEYWORDS):
        business, why = "Fail", f"Its industry ({d['industry']}) is on the excluded list."
        business_kind, business_note = "excluded", f"Excluded industry: {d['industry']}"
    elif np_pct is not None and np_pct >= REVENUE_LIMIT and not pre_revenue:
        business, why = "Fail", f"{np_pct:.1%} of revenue comes from non-permissible sources (limit is under 5%)."
    elif checked_by == "manual":
        business, why = "Pass", "Business activities checked manually (WattleFolio review list)."
    else:
        # Automatic check: pass only when nothing at all needs a closer look
        reasons = []
        if not d["industry"]:
            reasons.append("Yahoo Finance doesn't say what industry it is in.")
        elif any(k in industry for k in REVIEW_KEYWORDS):
            reasons.append(f"Its industry ({d['industry']}) often includes non-permissible sales.")
        flags = description_flags(d.get("summary"))
        if flags:
            reasons.append(f"Its company description mentions: {', '.join(flags)}.")
        elif not d.get("summary"):
            reasons.append("Yahoo Finance has no description of what it does.")
        if pre_revenue:
            reasons.append(f"It has almost no sales yet: {np_pct:.0%} of its revenue is interest on its cash, "
                           f"so the 5% revenue test doesn't say much. This is common for explorers and biotechs.")
        if np_pct is None:
            reasons.append("Its revenue figures are missing, so interest income can't be checked.")
        if reasons:
            business, why = "Review", f"Needs a manual check. {' '.join(reasons)} {MANUAL_CHECK_TIP}"
            business_kind = "review"
        else:
            business, why = "Pass", (f"Passed the automatic check: its industry ({d['industry']}) isn't a risky one, "
                                     f"its description mentions nothing non-permissible, and interest income is "
                                     f"{np_pct:.1%} of revenue.")

    r = {
        "Debt": ratio(d["debt"], d["avg_mcap"]),
        "Cash & interest-earning investments": ratio(d["cash"], d["avg_mcap"]),
        "Money owed to the company": ratio(d["receivables"], d["avg_mcap"]),
    }
    highest = max(r.values()) if all(v is not None for v in r.values()) else None

    if business == "Fail":
        tier = "Tier 3"
    elif highest is None:
        tier = "Incomplete"
    elif highest > TIER2_MAX:
        tier = "Tier 3"
    elif highest > TIER1_MAX:
        tier = "Tier 2"
    else:
        tier = "Tier 1"

    income = {"revenue": np_pct, "total": np_total, "source": np_source, "pre_revenue": pre_revenue,
              "na": None if np_pct is not None else "Revenue figures missing."}
    if business_kind == "review" and not any(k in industry for k in REVIEW_KEYWORDS) and d["industry"] and \
            not description_flags(d.get("summary")) and d.get("summary") and (pre_revenue or np_pct is None):
        business_kind = "pass"   # the only doubt is the income test, which has its own line
    out = {
        "tier": tier, "business": business, "business_kind": business_kind, "business_note": business_note,
        "checked_by": checked_by, "pre_revenue": pre_revenue, "why": why, "ratios": r, "highest": highest,
        "purge_pct": np_pct or 0.0, "purge_source": np_source, "income": income,
        "standards": standards(d, business_kind, business_note, income),
    }
    out["wattlefolio"] = wattlefolio_card(d, out)
    return out


# ----------------------------------------------------------------------------- full ETF holdings files
@st.cache_data(ttl=12 * 3600, show_spinner=False)
def download_file(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (WattleFolio Shariah Checker)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


@st.cache_data(ttl=3600, show_spinner=False)
def _saved_holdings(path, mtime):
    with open(path, "rb") as f:
        return parse_holdings_file(f.read(), path)


def saved_holdings(symbol):
    """A holdings file saved in etf_holdings/ named after the fund, e.g. VAS.AX.csv, SPY.xlsx (or VAS.csv)."""
    if not os.path.isdir(HOLDINGS_DIR):
        return None, ""
    wanted = {symbol.upper(), symbol.split(".")[0].upper()}
    for fn in sorted(os.listdir(HOLDINGS_DIR)):
        stem, ext = os.path.splitext(fn)
        if ext.lower() in (".csv", ".xlsx", ".xls") and stem.upper() in wanted:
            path = os.path.join(HOLDINGS_DIR, fn)
            try:
                return _saved_holdings(path, os.path.getmtime(path)), fn
            except Exception:
                return None, ""
    return None, ""


def provider_holdings(f):
    """Download the full list straight from the provider where there's a fixed link: SPDR (US) and BetaShares (ASX).

    Returns (parsed file, provider name) or (None, "")."""
    sym, family = f["symbol"], (f.get("family") or "").lower()
    if "." not in sym and ("spdr" in family or "state street" in family or sym.upper() in {"SPY", "SPLG", "MDY", "DIA"}):
        url, fn, who = SPDR_HOLDINGS_URL.format(sym.lower()), "spdr.xlsx", "State Street SPDR's daily holdings file"
    elif sym.upper().endswith(".AX") and "betashares" in family.replace(" ", ""):
        url, fn, who = BETASHARES_HOLDINGS_URL.format(sym.split(".")[0].upper()), "betashares.csv", "BetaShares' holdings file"
    else:
        return None, ""
    try:
        return parse_holdings_file(download_file(url), fn), who
    except Exception:
        return None, ""


def full_check_on():
    return st.session_state.get("etf_depth") == "full"


def fund_holdings(f, allow_upload=True, want_full=None):
    """Holdings list to check: (entries, where it came from, as-of date, is it the full list?).

    Quick check: Yahoo's top holdings. Full check: the provider's full list where one can be found.
    A file the visitor uploaded is always used."""
    up = st.session_state.get("uploaded_holdings", {}).get(f["symbol"].upper()) if allow_upload else None
    if up:
        return up["entries"], f"your uploaded file ({up['file']})", up["as_of"], True
    yahoo = [{"ticker": t, "name": n, "weight": w, "country": "", "cash": False} for t, n, w in f["holdings"]]
    if not (full_check_on() if want_full is None else want_full):
        return yahoo, "Yahoo Finance (top holdings only)", "", False
    saved, fn = saved_holdings(f["symbol"])
    if saved:
        return saved["entries"], f"the saved holdings file ({fn})", saved["as_of"], True
    auto, who = provider_holdings(f)
    if auto:
        return auto["entries"], who, auto["as_of"], True
    return yahoo, "Yahoo Finance (top holdings only; no full list found for this fund)", "", False


MIX_GROUPS = [("Compliant", "var(--wf-good)"), ("Needs review", "var(--wf-watch)"),
              ("Not compliant", "var(--wf-bad)"), ("Couldn't check", "var(--wf-muted)")]


def name_code(name, code):
    """How a share is shown everywhere: Company name (CODE)."""
    name, code = (name or "").strip(), (code or "").strip()
    if not code or name.upper() == code.upper():
        return name or code
    return f"{name} ({code})" if name else code


def holdings_mix(rows, extras):
    """Share of the whole fund (by weight) in each result group, plus extras such as cash or unchecked holdings."""
    mix = {g: {"weight": 0.0, "count": 0} for g, _ in MIX_GROUPS}
    for r in rows:
        mix[r["group"]]["weight"] += r["Weight"]
        mix[r["group"]]["count"] += 1
    watch = sum(r["Weight"] for r in rows if r["group"] == "Compliant" and r["tier"] == "Tier 2")
    return {"groups": mix, "extras": [x for x in extras if x[2] > 0.0005], "watch": watch, "total": len(rows)}


def check_holding(e, fund_symbol):
    for cand in yahoo_candidates(e["ticker"], e.get("country", ""), fund_symbol):
        d, _ = get_data(cand)
        if d is not None and d["kind"] == "stock":
            return d, screen(d)
    return None, None


def screen_fund(f, allow_upload=True, want_full=None):
    """Look through an ETF's holdings (the full list where available) and screen each one with the WattleFolio rules."""
    islamic = any(w in f["name"].lower() for w in ISLAMIC_FUND_WORDS)
    wanted = full_check_on() if want_full is None else want_full
    entries, source, as_of, full = fund_holdings(f, allow_upload, wanted)
    shares = sorted((e for e in entries if not e["cash"]), key=lambda e: -e["weight"])
    cash_w = sum(e["weight"] for e in entries if e["cash"])
    if full:   # largest first, until the target share of the fund is covered
        picked, cum = [], cash_w
        for e in shares:
            if cum >= ETF_FULL_TARGET or len(picked) >= ETF_FULL_MAX:
                break
            picked.append(e)
            cum += e["weight"]
    else:
        picked = shares

    ctx = get_script_run_ctx()
    with ThreadPoolExecutor(max_workers=6, initializer=lambda: add_script_run_ctx(None, ctx)) as pool:
        results = list(pool.map(lambda e: check_holding(e, f["symbol"]), picked))

    rows, failed, watch = [], [], False
    checked, purge_sum = 0.0, 0.0
    for e, (d, hs) in zip(picked, results):
        if d is None:
            rows.append({"Holding": e["name"], "Code": e["ticker"], "Weight": e["weight"], "Result": "Couldn't check",
                         "tier": "Incomplete", "group": "Couldn't check"})
            continue
        result = TIER_STYLE[hs["tier"]][0] + (" (needs manual check)" if hs["business"] == "Review" else "")
        group = ("Couldn't check" if hs["tier"] == "Incomplete" else "Not compliant" if hs["tier"] == "Tier 3"
                 else "Needs review" if hs["business"] == "Review" else "Compliant")
        rows.append({"Holding": d["name"], "Code": d["symbol"], "Weight": e["weight"], "Result": result,
                     "tier": hs["tier"], "group": group})
        if hs["tier"] == "Incomplete":
            continue
        checked += e["weight"]
        purge_sum += e["weight"] * hs["purge_pct"]
        if hs["tier"] == "Tier 3":
            failed.append(name_code(d["name"], d["symbol"]))
        watch = watch or hs["tier"] == "Tier 2"

    listed = sum(e["weight"] for e in entries)
    unchecked = sum(e["weight"] for e in shares[len(picked):])
    extras = [("Cash & other (not shares)", "var(--wf-line)", cash_w, "cash, futures, currency")]
    if full:
        extras.append(("Smaller holdings not checked", "var(--wf-bar)", unchecked, f"{len(shares) - len(picked)} holdings"))
    else:
        extras.append(("Not listed by Yahoo", "var(--wf-bar)", max(0.0, 1 - listed), "rest of the fund"))
    coverage_needed = ETF_FULL_TARGET if full else FUND_FULL_COVERAGE
    covered = checked + cash_w
    action, note = None, None
    if islamic:
        tier = "Tier 1"
        action = "An Islamic fund: it is screened by its own Shariah board. Check which standard it follows."
        why = "The fund's name says it is Shariah-compliant, so it follows its own Shariah board's rules."
        if failed:
            note = (f"Under the WattleFolio rules, {len(failed)} of its holdings would not be compliant "
                    f"({', '.join(failed[:8])}{' and others' if len(failed) > 8 else ''}). Its board may use a different standard.")
    elif f["bonds"] > FUND_MAX_BONDS:
        tier, why = "Tier 3", f"{f['bonds']:.0%} of the fund is in bonds, which pay interest."
    elif failed:
        tier = "Tier 3"
        why = (f"It holds {len(failed)} compan{'y that isn' if len(failed) == 1 else 'ies that aren'}'t compliant: "
               f"{', '.join(failed[:8])}{' and others' if len(failed) > 8 else ''}.")
    elif not entries:
        tier, why = "Incomplete", "Yahoo Finance doesn't list this fund's holdings."
        action = "Can't see what this fund holds, so it can't be checked."
    elif covered < coverage_needed:
        tier = "Incomplete"
        why = (f"The holdings that could be checked pass, but they're only {covered:.0%} of the fund. "
               f"The other {1 - covered:.0%} can't be checked here.")
        action = ("Its holdings pass so far, but not enough of the fund could be checked. Ask a scholar or use a "
                  "certified Islamic ETF." if full else
                  "Its top holdings pass, but most of the fund can't be seen in a quick check. Try the full check, "
                  "ask a scholar or use a certified Islamic ETF.")
    else:
        tier = "Tier 2" if watch else "Tier 1"
        why = ("Every holding checked passes the WattleFolio rules"
               + (f" ({covered:.0%} of the fund; the rest are small holdings)." if full and covered < 0.999 else "."))

    return {
        "tier": tier, "business": "Pass", "why": why, "action": action, "note": note,
        "purge_pct": purge_sum / checked if checked else 0.0,
        "purge_source": f"average of the holdings checked, {checked:.0%} of the fund" if checked else "",
        "rows": rows, "checked": checked, "islamic": islamic, "mix": holdings_mix(rows, extras),
        "source": source, "as_of": as_of, "full": full, "total_holdings": len(shares),
        "full_missing": wanted and not full,
    }


def evaluate(d):
    return screen_fund(d) if d["kind"] == "fund" else screen(d)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def market_universe(region):
    """The largest companies in a market from Yahoo's screener, leaving out the skipped sectors."""
    sectors = [x for x in ("Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive",
                           "Energy", "Financial Services", "Healthcare", "Industrials", "Real Estate", "Technology",
                           "Utilities") if x not in IDEAS_SKIP_SECTORS]
    query = yf.EquityQuery("and", [yf.EquityQuery("eq", ["region", region]),
                                   yf.EquityQuery("is-in", ["sector", *sectors])])
    quotes = yf.screen(query, size=IDEAS_CANDIDATES, sortField="intradaymarketcap", sortAsc=False).get("quotes", [])
    return [{"symbol": q["symbol"], "market_cap": q.get("marketCap"), "pe": q.get("trailingPE")}
            for q in quotes if q.get("symbol")]


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def etf_universe(region):
    """The largest ETFs in a market, plus any Islamic/Shariah/Halal ETFs found by name (they're often small)."""
    found = {}
    try:
        quotes = yf.screen(yf.ETFQuery("eq", ["region", region]), size=IDEAS_ETF_CANDIDATES,
                           sortField="fundnetassets", sortAsc=False).get("quotes", [])
        for q in quotes:
            if q.get("symbol"):
                found[q["symbol"]] = {"symbol": q["symbol"], "market_cap": q.get("netAssets") or q.get("totalAssets"), "pe": None}
    except Exception:
        pass
    suffix = MARKET_SUFFIX.get(region)
    for word in ISLAMIC_FUND_WORDS:
        try:
            hits = yf.Search(f"{word} etf", max_results=20).quotes
        except Exception:
            continue
        for q in hits:
            sym = q.get("symbol") or ""
            in_market = sym.endswith(suffix) if suffix else "." not in sym
            if q.get("quoteType") == "ETF" and in_market and sym not in found:
                found[sym] = {"symbol": sym, "market_cap": None, "pe": None}
    if not found:
        raise ValueError("no ETFs found for this market")
    return list(found.values())


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def market_ideas(region, kind="stock"):
    """Screen every company (or ETF) in the market universe. Saved for a day, so only the first visit is slow."""
    universe = etf_universe(region) if kind == "etf" else market_universe(region)
    ctx = get_script_run_ctx()

    def check(u):
        d, _ = get_data(u["symbol"])
        if d is None or d["kind"] != kind.replace("etf", "fund"):
            return None
        y = d["dps_12m"] / d["price"] if d.get("dps_12m") and d["price"] else 0.0
        if kind == "etf":
            s = screen_fund(d, allow_upload=False, want_full=False)
            return {"d": d, "s": s, "market_cap": d.get("size") or u["market_cap"], "pe": None, "yield": y,
                    "ret_1y": d.get("ret_1y"), "fee": d.get("fee"), "debt": None, "highest": None}
        s = screen(d)
        return {"d": d, "s": s, "market_cap": u["market_cap"],
                "pe": u["pe"] if u["pe"] and u["pe"] > 0 else None, "yield": y,
                "ret_1y": d.get("ret_1y"), "fee": None, "debt": s["ratios"]["Debt"], "highest": s["highest"]}

    with ThreadPoolExecutor(max_workers=6, initializer=lambda: add_script_run_ctx(None, ctx)) as pool:
        rows = [r for r in pool.map(check, universe) if r is not None]
    return rows, len(universe), datetime.now().strftime("%d %b %Y %H:%M")


# Sort options: label -> (row key, largest first?)
IDEA_SORTS = {
    "Biggest companies": ("market_cap", True),
    "Highest dividend yield": ("yield", True),
    "Best 1-year price change": ("ret_1y", True),
    "Lowest P/E (cheapest vs profits)": ("pe", False),
    "Lowest debt": ("debt", False),
    "Most room under the Shariah limits": ("highest", False),
}
ETF_SORTS = {
    "Biggest funds": ("market_cap", True),
    "Highest dividend yield": ("yield", True),
    "Best 1-year price change": ("ret_1y", True),
    "Lowest fees": ("fee", False),
}


def research_links(d):
    """Where to check a company or fund yourself: (label, url) pairs."""
    sym, stock = d["symbol"], d["kind"] == "stock"
    code = sym.split(".")[0]
    yahoo = f"https://finance.yahoo.com/quote/{sym}"
    links = [("Yahoo Finance", f"{yahoo}/")]
    if stock:
        links += [("Financials", f"{yahoo}/financials/"), ("Company profile", f"{yahoo}/profile/")]
    if sym.upper().endswith(".AX"):
        links.append(("Annual reports (ASX)" if stock else "ASX announcements",
                      f"https://www.asx.com.au/markets/trade-our-cash-market/announcements.{code.lower()}"))
    elif sym.upper().endswith(".KL"):
        links.append(("Annual reports (Bursa)",
                      f"https://www.bursamalaysia.com/market_information/announcements/company_announcement?company={code}"))
    elif "." not in sym and stock:
        links.append(("Annual reports (SEC 10-K)",
                      f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}&type=10-K&dateb=&owner=include&count=40"))
    if d.get("website"):
        links.append(("Company website" if stock else "Fund website", d["website"]))
    what = "annual report" if stock else "ETF product disclosure statement"
    links.append((f"Search: {what}", f"https://www.google.com/search?q={quote_plus(d['name'] + ' ' + what)}"))
    return links


def key_figures(d):
    """Eight basic details (two even rows of four, or four rows of two on a phone)."""
    tiles = []
    change = ""
    if d.get("prev_close"):
        pct = d["price"] / d["prev_close"] - 1
        change = f'<span class="{"up" if pct >= 0 else "down"}">{pct:+.2%} today</span>'
    p, pc = to_display(d["price"], d["price_ccy"])
    tiles.append(("Price", f'<span class="nw">{num_text(p)}</span> {pc}', change or f"on {d['price_date']}"))
    if d["kind"] == "stock":
        tiles.append(("Market value", money(d.get("market_cap_quote"), d["price_ccy"]), d.get("exchange_name") or ""))
        tiles.append(("P/E ratio", f"{d['pe']:.1f}" if d.get("pe") else "–", "price vs last 12 months' profit"))
    else:
        tiles.append(("Fund size", money(d.get("size"), d["price_ccy"]), d.get("family") or d.get("exchange_name") or ""))
        tiles.append(("Fees", f"{d['fee']:.2%} a year" if d.get("fee") is not None else "–", "annual management cost"))
    dy = d["dps_12m"] / d["price"] if d.get("dps_12m") and d["price"] else 0.0
    tiles.append(("Dividend yield", f"{dy:.2%}", "last 12 months"))
    dps, dps_ccy = to_display(d.get("dps_12m"), d["price_ccy"])
    tiles.append(("Dividend per share", f'<span class="nw">{num_text(dps)}</span> {dps_ccy}' if dps is not None else "–",
                  "paid in the last 12 months"))
    if d.get("low_52w") and d.get("high_52w"):
        lo, hi = to_display(d["low_52w"], d["price_ccy"])[0], to_display(d["high_52w"], d["price_ccy"])[0]
        tiles.append(("52-week range", f'<span class="nw">{num_text(lo)} –</span> <span class="nw">{num_text(hi)}</span>',
                      to_display(1, d["price_ccy"])[1]))
    else:
        tiles.append(("52-week range", "–", "lowest – highest price"))
    if d.get("ret_1y") is not None:
        tiles.append(("1-year change", f'<span class="{"up" if d["ret_1y"] >= 0 else "down"}">{d["ret_1y"]:+.1%}</span>',
                      "share price"))
    else:
        tiles.append(("1-year change", "–", "share price"))
    if d["kind"] == "stock":
        tiles.append(("Industry", f'<span class="txt">{html.escape(d.get("industry") or "–")}</span>',
                      d.get("sector") or ""))
    else:
        stocks = d.get("stocks")
        tiles.append(("Invested in shares", f"{stocks:.0%}" if stocks is not None else "–",
                      f"bonds {d['bonds']:.0%}" if stocks is not None else "rest in cash or bonds"))
    st.markdown('<div class="stats">' + "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div>'
        f'<div class="s">{sub if sub.startswith("<span") else html.escape(sub)}</div></div>'
        for k, v, sub in tiles) + "</div>", unsafe_allow_html=True)
    shown, target = to_display(1, d["price_ccy"])
    if target != d["price_ccy"]:
        st.caption(f"Converted from {d['price_ccy']} at 1 {d['price_ccy']} = {shown:,.4f} {target} "
                   f"(Yahoo Finance, refreshed hourly). Ratios are the same in any currency.")


def research_buttons(d):
    st.markdown('<div class="links">' + "".join(
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{html.escape(label)} ↗</a>'
        for label, url in research_links(d)) + "</div>", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- display helpers
def verdict_card(d, s):
    label, bg, fg, action = TIER_STYLE[s["tier"]]
    action = s.get("action") or action
    tier_txt = "" if s["tier"] == "Incomplete" or s.get("islamic") else f" · {s['tier']}"
    st.markdown(f"""
    <div class="verdict" style="background:{bg};color:{fg}">
      <div class="label">{label}</div>
      <div class="action">{action}</div>
      <div class="who">{html.escape(d['name'])} ({html.escape(d['symbol'])}){tier_txt}</div>
    </div>""", unsafe_allow_html=True)
    note = s["why"] if s["business"] == "Review" else s.get("note")
    if note:
        st.markdown(f'<div class="note">{html.escape(note)}</div>', unsafe_allow_html=True)


def ratio_bar(name, value):
    if value is None:
        st.markdown(f'<div class="row"><span>{name}</span><span>missing</span></div>', unsafe_allow_html=True)
        return
    colour = "var(--wf-good)" if value <= TIER1_MAX else "var(--wf-watch)" if value <= TIER2_MAX else "var(--wf-bad)"
    width = min(value / 0.5, 1) * 100   # bar spans 0-50%
    st.markdown(f"""
    <div class="row"><span>{name}</span><span><b>{value:.1%}</b></span></div>
    <div class="bar"><div class="fill" style="width:{width:.1f}%;background:{colour}"></div>
      <div class="tick" style="left:{TIER1_MAX/0.5*100:.1f}%"></div>
      <div class="tick" style="left:{TIER2_MAX/0.5*100:.1f}%"></div></div>""", unsafe_allow_html=True)


def pill(text, tier):
    _, bg, fg, _ = TIER_STYLE[tier]
    return f'<span class="pill" style="background:{bg};color:{fg}">{html.escape(text)}</span>'


MARKS = {"pass": "✓", "fail": "✗", "warn": "!", "watch": "!", "na": "–"}


def cards(items):
    """items: [(title, pill_html, subtitle_html, [(label, value_html, status)])] -> grid of cards (1 column on phones).

    status: True/"fail" (red ✗), "pass" (✓), "warn"/"watch" (amber !), "na" (–), False/"" (no mark)."""
    out = []
    for title, badge, sub, lines in items:
        rows = []
        for label, value, status in lines:
            status = "fail" if status is True else ("" if status is False else status)
            mark = f'<i class="mk {status}">{MARKS[status]}</i>' if status in MARKS else ""
            rows.append(f'<div class="line {status}"><span>{mark}{label}</span><span>{value}</span></div>')
        rows = "".join(rows)
        out.append(f'<div class="card"><div class="head"><span>{title}</span>{badge}</div>'
                   f'<div class="sub">{sub}</div>{rows}</div>')
    st.markdown(f'<div class="cards">{"".join(out)}</div>', unsafe_allow_html=True)


RESULT_TIER = {"Pass": "Tier 1", "Fail": "Tier 3", "Needs a look": "Tier 2"}


def standards_table(s):
    items = [("WattleFolio rules", pill(s["wattlefolio"]["result"], s["tier"]),
              f'vs {s["wattlefolio"]["basis"]}<div class="why">{html.escape(s["wattlefolio"]["summary"])}</div>',
              s["wattlefolio"]["tests"])]
    items += [(name, pill(x["result"], RESULT_TIER.get(x["result"], "Incomplete")),
               f'vs {x["basis"]}<div class="why">{html.escape(x["summary"])}</div>', x["tests"])
              for name, x in s["standards"].items()]
    cards(items)
    st.caption("✓ passes · ✗ fails · ! needs a look · – figure missing. The income test is non-permissible income, which must be under 5%. The verdict at the top follows the WattleFolio "
               "rules; the other standards are for comparison. Non-permissible income is estimated from interest "
               "income unless a figure is in the review list; AAOIFI measures it against total income (revenue plus "
               "interest), the others against revenue. \"Cash\" is all of the company's cash; the standards only count "
               "interest-earning cash, so cash-rich companies can look worse than they really are.")


def mix_bar(s):
    """Stacked bar and legend: how much of the fund is compliant, needs review, not compliant, unknown."""
    mix = s.get("mix")
    if not mix or not mix["total"]:
        return
    parts = [(g, colour, mix["groups"][g]["weight"], mix["groups"][g]["count"]) for g, colour in MIX_GROUPS]
    parts += [(label, colour, w, note) for label, colour, w, note in mix["extras"]]
    bar = "".join(f'<div style="width:{w * 100:.2f}%;background:{c}"></div>' for _, c, w, _ in parts if w > 0)
    legend = []
    for g, c, w, n in parts:
        if w <= 0 and g != "Compliant":
            continue
        extra = f" ({mix['watch']:.1%} on watch)" if g == "Compliant" and mix["watch"] > 0 else ""
        count = n if isinstance(n, str) else f"{n} holding{'s' if n != 1 else ''}"
        legend.append(f'<div class="line"><span><i class="dot" style="background:{c}"></i>{g}{extra}</span>'
                      f'<span><b>{w:.1%}</b> <small>· {count}</small></span></div>')
    st.markdown(f'<div class="mix"><div class="mixbar">{bar}</div>{"".join(legend)}</div>', unsafe_allow_html=True)


def holdings_upload(d, s, key="check"):
    """Upload a provider's full holdings file for this ETF (kept for this visit)."""
    sym = d["symbol"].upper()
    uploaded = st.session_state.setdefault("uploaded_holdings", {})
    with st.expander("Check every holding: add the full holdings file", expanded=sym in uploaded):
        st.write("Fund providers publish every holding, usually updated daily. Download the file and add it here:")
        st.markdown("- **Vanguard**: the ETF's page on vanguard.com.au → *Portfolio* → export the holdings list\n"
                    "- **BetaShares**: the fund's page on betashares.com.au → *Holdings* → download the full holdings (CSV)\n"
                    "- **SPDR** (e.g. SPY): downloaded automatically. On ssga.com: the fund's page → *Holdings* → "
                    "*Daily holdings* (Excel)")
        st.caption(f"An uploaded file is used for this visit only. To keep it for everyone, add it to the "
                   f"`{HOLDINGS_DIR}` folder on GitHub named after the fund, e.g. `{sym}.csv` or `{sym}.xlsx`.")
        gen = st.session_state.get("holdings_upload_gen", 0)   # bumped to empty the upload box
        f = st.file_uploader("Holdings file (CSV or Excel)", type=["csv", "xlsx", "xls"], key=f"holdings_file_{key}_{sym}_{gen}")
        if f is not None and uploaded.get(sym, {}).get("id") != (f.name, f.size):
            try:
                parsed = parse_holdings_file(f.getvalue(), f.name)
            except Exception as e:
                st.error(f"Couldn't read that file ({e}). Use the full holdings download from the fund's own website.")
            else:
                uploaded[sym] = {"id": (f.name, f.size), "file": f.name, **parsed}
                st.rerun()
        if sym in uploaded and st.button("Stop using the uploaded file", key=f"holdings_clear_{key}_{sym}"):
            uploaded.pop(sym, None)
            st.session_state["holdings_upload_gen"] = gen + 1
            st.rerun()


def fund_detail(d, s, place="check", key="check"):
    st.subheader("Why")
    st.write(s["why"])
    if s["rows"]:
        st.subheader("What's inside")
        mix_bar(s)
        dated = f", as of {s['as_of']}" if s.get("as_of") else ""
        if s["full"]:
            st.caption(f"Holdings from {s['source']}{dated}: {s['total_holdings']} shares in total. The largest "
                       f"{len(s['rows'])} were checked with the WattleFolio rules, covering {s['checked']:.0%} of the fund.")
        elif s.get("full_missing"):
            st.caption(f"No full holdings list could be found for this fund, so these are the {len(s['rows'])} biggest "
                       f"holdings Yahoo Finance publishes. Add the fund's full holdings file below to check the rest.")
        else:
            n = len(s["rows"])
            st.caption(f"Quick check: the {'biggest holding' if n == 1 else f'{n} biggest holdings'} Yahoo Finance "
                       f"publishes, checked with the WattleFolio rules. Choose the full check above to look at every holding.")

        def holding_rows(rows):
            return "".join(
                f'<div class="holding"><div><div class="nm">{html.escape(name_code(r["Holding"], r["Code"]))}</div>'
                f'<div class="meta">{pill(r["Result"], r["tier"])}</div></div>'
                f'<div class="wt">{r["Weight"]:.1%}</div></div>' for r in rows)
        st.markdown(holding_rows(s["rows"][:15]), unsafe_allow_html=True)
        if len(s["rows"]) > 15:
            with st.expander(f"Show all {len(s['rows'])} holdings checked"):
                st.markdown(holding_rows(s["rows"][15:]), unsafe_allow_html=True)
        names = {r["Code"]: r["Holding"] for r in s["rows"] if r["Result"] != "Couldn't check"}
        if names:
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
            pick = c1.selectbox("Check one of these holdings", list(names), index=None, key=f"pick_{key}",
                                format_func=lambda c: f"{names[c]} ({c})", placeholder="Choose a holding")
            c2.button("Open →", key=f"pick_open_{key}", disabled=pick is None, on_click=open_check,
                      args=(place, pick, names[pick]) if pick else (place, "", ""))
    holdings_upload(d, s, key)

    st.subheader("Cleaning dividends")
    if s["checked"]:
        already = " if the fund doesn't already do this for you" if s["islamic"] else ""
        st.write(f"Give **{s['purge_pct']:.2%}** of every distribution from this fund to charity{already} "
                 f"({s['purge_source']}).")
    else:
        st.write("Can't be worked out without the fund's holdings.")

    with st.expander("More detail"):
        sectors = sorted(d["sectors"].items(), key=lambda kv: -kv[1])
        if sectors:
            st.write("Sectors: " + " · ".join(f"{k.replace('_', ' ').title()} {v:.0%}" for k, v in sectors))
        st.write(f"Bonds: {d['bonds']:.0%} of the fund")
        st.caption(f"Holdings source: {s['source']}" + (f", as of {s['as_of']}" if s.get("as_of") else "") + ".")
        source_detail(d)


def rules_cards():
    """The WattleFolio rules next to each standard's limits, built from the settings at the top."""
    def lim(x):
        return f"under {x * 100:g}%"
    ours = f"{TIER1_MAX * 100:g}% <small>(watch to {TIER2_MAX * 100:g}%)</small>"
    revenue = ("Non-permissible revenue", lim(REVENUE_LIMIT), False)
    djim_extra = "*" if DJIM_TEST_CASH_RECEIVABLES else ""
    cards([
        ("WattleFolio rules", "", "vs 2-year average market value",
         [("Debt", ours, False), ("Cash", ours, False), ("Money owed", ours, False), revenue]),
        ("AAOIFI", "", "vs current market value",
         [("Debt", lim(AAOIFI_LIMIT), False), ("Cash", lim(AAOIFI_LIMIT), False), ("Money owed", "not tested", False), revenue]),
        ("Dow Jones Islamic", "", "vs 2-year average market value",
         [("Debt", lim(DJIM_LIMIT), False),
          ("Cash", lim(DJIM_LIMIT) + djim_extra if DJIM_TEST_CASH_RECEIVABLES else "not tested", False),
          ("Money owed", lim(DJIM_LIMIT) + djim_extra if DJIM_TEST_CASH_RECEIVABLES else "not tested", False), revenue]),
        ("S&amp;P Shariah", "", "vs 3-year average market value",
         [("Debt", lim(SP_LIMIT), False), ("Cash", lim(SP_LIMIT), False),
          ("Money owed + cash", lim(SP_RECEIVABLES_LIMIT), False), revenue]),
        ("MSCI Islamic", "", "vs total assets",
         [("Debt", lim(MSCI_LIMIT), False), ("Cash", lim(MSCI_LIMIT), False),
          ("Money owed + cash", lim(MSCI_LIMIT), False), revenue]),
    ])


def source_links(d):
    return " · ".join(f"[{label}]({url})" for label, url in research_links(d) if not label.startswith("Search"))


def source_line(d):
    """One short line under the verdict: when the price and figures are from."""
    if d["kind"] == "fund":
        parts = [f"Price {d['price_date']}", "holdings as listed on Yahoo Finance"]
    else:
        parts = [f"Price {d['price_date']}", f"balance sheet {d['as_of']}",
                 d["inc_label"].split(" (")[0].replace(" annual report", " revenue")]
    st.markdown(f'<div class="src">Source: Yahoo Finance · {" · ".join(html.escape(p) for p in parts)}</div>',
                unsafe_allow_html=True)


def source_detail(d):
    """Full list of where each figure comes from, for the "More detail" section."""
    lines = [f"- **Share price:** {d['price_ccy']} {d['price']:,.3f} on {d['price_date']} "
             f"(refreshed every 15 minutes, may be delayed about 20 minutes by the exchange)"]
    if d["kind"] == "stock":
        lines += [f"- **Debt, cash, money owed, total assets:** all from the same balance sheet, at {d['bs_label']}. "
                  f"That's the newest balance sheet Yahoo Finance has that reports all of them.",
                  f"- **Revenue and interest income:** both from the {d['inc_label']}",
                  f"- **Market value** (what each figure is compared with): the average of month-end share prices "
                  f"over the last 2 years (3 for S&P), × {d['shares']:,.0f} shares on issue now. Averaging smooths "
                  f"out short price swings, as the index providers do; AAOIFI uses today's value instead"]
        if d.get("receivables_missing"):
            lines.append("- That balance sheet doesn't report money owed to the company, so it's counted as 0")
        if d.get("interest_missing"):
            lines.append("- That annual report doesn't show interest income, so the dividend-cleaning estimate is 0")
        if d.get("fin_ccy_assumed"):
            lines.append(f"- Yahoo didn't say which currency the accounts are in, so {d['fin_ccy']} (the share price "
                         f"currency) is assumed. Check the annual report if the company reports in another currency")
    lines.append(f"- **Source:** {source_links(d)}. Company figures are refreshed every 6 hours.")
    st.markdown("**Where these figures come from**\n\n" + "\n".join(lines))


def to_display(x, ccy):
    """Convert an amount into the chosen display currency. Returns (amount, currency)."""
    target = st.session_state.get("display_ccy")
    if x is None or not ccy or not target or ccy == target:
        return x, ccy
    try:
        return x * fx_rate(ccy, target), target
    except Exception:
        return x, ccy   # no exchange rate available: keep the original currency


def num_text(x):
    """A price-sized number: no decimals from 1,000 up, 3 below 1."""
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}" if abs(x) >= 1 else f"{x:,.3f}"


def price_text(x, ccy):
    x, ccy = to_display(x, ccy)
    return "–" if x is None else f"{num_text(x)} {ccy}"


def money(x, ccy=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    x, ccy = to_display(x, ccy)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(x) >= div:
            return f"{x/div:,.1f}{suffix} {ccy}".strip()
    return f"{x:,.2f} {ccy}".strip()


@st.cache_data(ttl=15 * 60, show_spinner=False)
def fetch_failure(symbol):
    """Why a code couldn't be fetched (or None), remembered for 15 minutes so a missing code isn't retried
    on every tap, e.g. the "AAPL.AX" the app tries first for an ASX fund's US holdings."""
    try:
        fetch(symbol)
        return None
    except Exception as e:
        return str(e) or type(e).__name__


def get_data(symbol):
    failure = fetch_failure(symbol)
    if failure:
        return None, f"Couldn't get figures for {symbol} right now ({failure}). Check the code, or try again in a few minutes."
    try:
        cached = fetch(symbol)
        if cached.get("profile_missing") and time.time() - cached.get("fetched_ts", 0) > PROFILE_RETRY_MINUTES * 60:
            fetch.clear(symbol)   # incomplete result: ask Yahoo again after a while instead of keeping it 6 hours
            fetch_failure.clear(symbol)
            cached = fetch(symbol)
        d = dict(cached)
    except Exception as e:
        return None, f"Couldn't get figures for {symbol} right now ({e}). Check the code, or try again in a few minutes."
    try:
        raw, prev, when = quote(symbol)
        d["price"], d["price_date"] = raw / d["divisor"], when
        d["prev_close"] = prev / d["divisor"] if prev else None
        if d["kind"] == "stock":
            d["mcap"] = raw * d["shares"] * d["conv"]
    except Exception:
        pass   # keep the price from the main fetch
    return d, None


# ----------------------------------------------------------------------------- portfolio storage (in the link)
def read_holdings():
    raw = st.query_params.get("h", "")
    rows = []
    for part in [p for p in raw.split(";") if p]:
        bits = part.split(":") + ["", "", "", ""]
        try:
            rows.append({"Code": bits[0].upper(), "Shares": float(bits[1] or 0),
                         "Dividend per share (last 12 months)": float(bits[2]) if bits[2] else None,
                         "Date it became not compliant": datetime.strptime(bits[3], "%Y-%m-%d").date() if bits[3] else None})
        except ValueError:
            continue
    return pd.DataFrame(rows, columns=["Code", "Shares", "Dividend per share (last 12 months)", "Date it became not compliant"])


def write_holdings(df):
    parts = []
    for _, r in df.iterrows():
        code = str(r["Code"] or "").strip().upper()
        if not code or code == "NONE":
            continue
        shares = r["Shares"] if pd.notna(r["Shares"]) else 0
        dps = r["Dividend per share (last 12 months)"]
        dps = "" if pd.isna(dps) else f"{dps:g}"
        bd = r["Date it became not compliant"]
        bd = bd.strftime("%Y-%m-%d") if pd.notna(bd) and bd else ""
        parts.append(f"{code}:{shares:g}:{dps}:{bd}")
    new = ";".join(parts)
    if new != st.query_params.get("h", ""):
        if new:
            st.query_params["h"] = new
        elif "h" in st.query_params:
            del st.query_params["h"]


# ----------------------------------------------------------------------------- pages
@st.cache_data
def image_uri(path):
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def brand_header():
    try:
        light, dark = image_uri("assets/wattlefolio-logo.png"), image_uri("assets/wattlefolio-logo-dark.png")
    except OSError:
        st.title("WattleFolio Shariah Checker")
        return
    mode = theme_mode()
    if mode:
        logo = f'<img src="{dark if mode == "dark" else light}" alt="WattleFolio">'
    else:
        logo = (f'<picture><source srcset="{dark}" media="(prefers-color-scheme: dark)">'
                f'<img src="{light}" alt="WattleFolio"></picture>')
    st.markdown(f'<div class="brand">{logo}<span>Shariah Checker</span></div>', unsafe_allow_html=True)


brand_header()

KEPT_CONTROLS = ["check_query", "check_pick", "ideas_etf", "ideas_market", "ideas_sort_stock", "ideas_sort_etf",
                 "ideas_watch_stock", "ideas_watch_etf", "ideas_manual_stock", "ideas_manual_etf"]
for _k in KEPT_CONTROLS:   # re-saving a control's value stops Streamlit discarding it while it isn't shown
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]
st.session_state.setdefault("ideas_manual_stock", True)

if "etf_depth" not in st.session_state:   # quick or full ETF check, remembered in the link as ?etf=full
    st.session_state["etf_depth"] = "full" if st.query_params.get("etf") == "full" else "quick"

_ccy_options = ["Original currency"] + DISPLAY_CURRENCIES
_ccy_saved = st.query_params.get("ccy", "")
if "ccy_choice" not in st.session_state:   # first visit: start from the currency saved in the link
    st.session_state["ccy_choice"] = _ccy_saved if _ccy_saved in DISPLAY_CURRENCIES else "Original currency"
_ccy = st.selectbox("Show prices in", _ccy_options, key="ccy_choice",
                    help="Converts prices and values using Yahoo Finance exchange rates. Saved in this page's link.")
st.session_state["display_ccy"] = _ccy if _ccy in DISPLAY_CURRENCIES else None
if (_ccy if _ccy in DISPLAY_CURRENCIES else "") != _ccy_saved:
    if _ccy in DISPLAY_CURRENCIES:
        st.query_params["ccy"] = _ccy
    elif "ccy" in st.query_params:
        del st.query_params["ccy"]

tab_check, tab_ideas, tab_watch, tab_mine, tab_how = st.tabs(["Check", "Find", "Watchlist", "Holdings", "How it works"])

# ----------------------------------------------------------------------------- watchlist (kept in the link as ?w=)
def watchlist():
    if "watchlist" not in st.session_state:
        st.session_state["watchlist"] = [c for c in st.query_params.get("w", "").split(",") if c]
    return st.session_state["watchlist"]


def save_watchlist():
    wl = watchlist()
    if wl:
        st.query_params["w"] = ",".join(wl)
    elif "w" in st.query_params:
        del st.query_params["w"]


def toggle_watch(symbol):
    wl = watchlist()
    if symbol in wl:
        wl.remove(symbol)
    else:
        wl.append(symbol)
    save_watchlist()


def watch_button(symbol, key, short=False):
    on = symbol in watchlist()
    label = ("★ Watching" if on else "☆ Watch") if short else \
            ("★ On your watchlist · Remove" if on else "☆ Add to watchlist")
    st.button(label, key=f"watch_{key}_{symbol}", on_click=toggle_watch, args=(symbol,), width="stretch" if short else "content",
              help="Remove from your watchlist" if on else "Add to your watchlist for a quick overview")


def based_on(d, s):
    """What a result rests on, in one or two short lines: reports used and when WattleFolio checked."""
    if d["kind"] == "fund":
        src = s.get("source", "Yahoo Finance")
        dated = f", as of {s['as_of']}" if s.get("as_of") else ""
        used = f"Holdings from {src}{dated}"
        old = False
    else:
        used = f"{d['inc_label'].split(' (')[0]} · balance sheet {d['as_of']}"
        old = d.get("bs_days", 0) > STALE_DAYS or d.get("inc_days", 0) > STALE_DAYS
    checked = f"Checked by WattleFolio {d.get('checked_at', '')} · price {d['price_date']}"
    return used, checked, old


# ----------------------------------------------------------------------------- opening a check from another list
def nav_stack(place):
    """What has been opened from a tab, newest last: [(symbol, name)]."""
    return st.session_state.setdefault(f"nav_{place}", [])


def open_check(place, symbol, name):
    nav_stack(place).append((symbol, name_code(name, symbol)))
    st.session_state[f"last_opened_{place}"] = symbol


SCROLL_TOP_JS = """<script>
  const doc = window.parent.document;
  for (const el of [doc.querySelector('[data-testid="stMain"]'), doc.querySelector('section.main'),
                    doc.scrollingElement]) { if (el) el.scrollTo({top: 0}); }
</script>"""


def scroll_to_top():
    if hasattr(st, "iframe"):   # fixed script written here, never user content
        st.iframe(SCROLL_TOP_JS, height=1)
    else:
        components.html(SCROLL_TOP_JS, height=0)


def navigated(place, home):
    """If something was opened from this tab, show its full check with a back button. True if one is showing."""
    stack = nav_stack(place)
    if not stack:
        return False
    back_to = home if len(stack) == 1 else stack[-2][1]
    if st.session_state.get(f"scrolled_{place}") != len(stack):   # jump to the top once per newly opened check
        st.session_state[f"scrolled_{place}"] = len(stack)
        scroll_to_top()
    st.button(f"← Back to {back_to}", key=f"back_{place}_{len(stack)}", on_click=stack.pop)
    show_result(stack[-1][0], place, f"{place}{len(stack)}")
    st.button(f"← Back to {back_to}", key=f"back2_{place}_{len(stack)}", on_click=stack.pop)
    return True


def show_result(symbol, place, key):
    """The full check of one stock or ETF. `place` is the tab showing it, `key` keeps its controls unique."""
    with st.spinner("Checking the figures…"):
        d, err = get_data(symbol)
    if d is not None and key == "check":   # what "← Back to …" says after opening something from this result
        st.session_state["check_label"] = name_code(d["name"], d["symbol"])
    if d is not None and d.get("profile_missing"):
        st.info("Yahoo Finance didn't send this company's profile this time (industry, description and reporting "
                "currency), so the business check needs a manual review. The financial figures are unaffected. "
                f"The app asks Yahoo again after {PROFILE_RETRY_MINUTES} minutes.")
    if err:
        st.error(err)
    elif d["kind"] == "fund":
        def _keep_depth():   # the setting outlives the control, which only exists on ETF pages
            st.session_state["etf_depth"] = st.session_state[f"etf_depth_choice_{key}"]
        st.radio("How thoroughly to check this ETF", list(ETF_DEPTHS), format_func=ETF_DEPTHS.get,
                 index=list(ETF_DEPTHS).index(st.session_state["etf_depth"]),
                 key=f"etf_depth_choice_{key}", on_change=_keep_depth, horizontal=True,
                 help="Quick uses the top holdings Yahoo Finance lists (usually 10). Full uses the fund "
                      "provider's complete list where it can be found and checks up to "
                      f"{ETF_FULL_MAX} holdings, which can take a minute the first time.")
        if (st.query_params.get("etf") == "full") != full_check_on():
            if full_check_on():
                st.query_params["etf"] = "full"
            else:
                del st.query_params["etf"]
        with st.spinner("Checking every holding… this can take a minute the first time." if full_check_on()
                        else "Checking what the fund holds…"):
            s = screen_fund(d)
        verdict_card(d, s)
        watch_button(d["symbol"], key)
        key_figures(d)
        source_line(d)
        research_buttons(d)
        fund_detail(d, s, place, key)
    else:
        s = screen(d)
        verdict_card(d, s)
        watch_button(d["symbol"], key)
        key_figures(d)
        source_line(d)
        research_buttons(d)

        old = ([f"balance sheet at {d['as_of']}"] if d["bs_days"] > STALE_DAYS else []) + \
              ([f"revenue from the {d['inc_label']}"] if d["inc_days"] > STALE_DAYS else [])
        if old:
            st.warning(f"Some figures are over a year old ({' and '.join(old)}). Yahoo Finance may be missing "
                       f"the latest reports, so check the company's latest annual report too: "
                       f"{source_links(d)}.")

        st.subheader("Why")
        if s["business"] == "Fail":
            st.write(s["why"])
        else:
            st.write("Each figure below is compared with the company's average value over the last 2 years. "
                     "Under 30% is compliant, 30–33% is on watch, over 33% is not compliant.")
            for name, v in s["ratios"].items():
                ratio_bar(name, v)

        st.subheader("Cleaning dividends")
        st.write(f"Give **{s['purge_pct']:.2%}** of every dividend from this company to charity "
                 f"({s['purge_source'] or 'no revenue data'}).")
        if s.get("pre_revenue"):
            st.caption("This is high because the company has almost no sales yet, so most of its income is "
                       "interest on its cash. Companies at this stage rarely pay dividends.")

        with st.expander("More detail"):
            by = f" ({s['checked_by']})" if s["business"] != "Review" else ""
            st.write(f"Business check: **{s['business']}{by}**. {s['why']}")
            ccy_note = f"{d['fin_ccy']} (assumed)" if d.get("fin_ccy_assumed") else d["fin_ccy"]
            st.write(f"Industry: {d['industry'] or 'not provided by Yahoo'} · Accounts reported in {ccy_note}")
            fc = d["fin_ccy"]
            st.write(f"Debt {money(d['debt'], fc)} · Cash & investments {money(d['cash'], fc)} · "
                     f"Money owed {money(d['receivables'], fc)} · 2-year average value {money(d['avg_mcap'], fc)} · "
                     f"Total assets {money(d['assets'], fc)}")
            st.markdown("**How other standards see it**")
            standards_table(s)
            source_detail(d)


def check_tab():
    query = st.text_input("Company, ETF or share code", placeholder="e.g. Woolworths, BHP.AX, Apple, SPUS",
                          key="check_query")
    if query:
        matches = search(query.strip())
        exact = query.strip().upper()
        if (not matches and "." in exact) or (matches and exact in [m[0] for m in matches]):
            symbol = exact
        elif matches:
            if st.session_state.get("check_pick") not in matches:
                st.session_state.pop("check_pick", None)
            symbol = st.selectbox("Pick the company or ETF", matches, format_func=lambda m: m[1], key="check_pick")[0]
        else:
            symbol = None
            st.warning("Nothing found. Try the full name, or the share code (ASX codes end in .AX).")
        if symbol:
            show_result(symbol, "check", "check")


with tab_check:
    if not navigated("check", st.session_state.get("check_label") or "your search"):
        check_tab()

def ideas_tab():
    etf_mode = st.checkbox("ETFs only", key="ideas_etf", help="Look through the largest ETFs in the market instead of companies")
    things = "ETFs" if etf_mode else "companies"
    st.write(f"The largest {things} in a market, screened with the WattleFolio rules. The top {IDEAS_SHOW} that pass "
             "are listed, sorted the way you choose.")
    st.caption(f"These are ideas to research, not recommendations or financial advice. They're ranked on figures "
               f"only, so check each one yourself before buying.")
    market = st.selectbox("Market", list(IDEAS_MARKETS), key="ideas_market")
    sorts = ETF_SORTS if etf_mode else IDEA_SORTS
    c1, c2 = st.columns(2)
    kind = "etf" if etf_mode else "stock"
    sort_by = c1.selectbox("Sort by", list(sorts), key=f"ideas_sort_{kind}")
    include_watch = c2.checkbox("Include \"on watch\" " + ("ETFs" if etf_mode else "stocks"), key=f"ideas_watch_{kind}")
    include_manual = c2.checkbox("Include ETFs that can't be fully checked" if etf_mode
                                 else "Include stocks that need a manual business check", key=f"ideas_manual_{kind}")

    count = IDEAS_ETF_CANDIDATES if etf_mode else IDEAS_CANDIDATES
    if st.session_state.get("ideas_search") != (market, kind):
        if st.button(f"Find {things} in {market}", type="primary"):
            st.session_state["ideas_search"] = (market, kind)
            st.rerun()
        st.caption(f"Checks the {count} largest {things}" + (" plus Islamic ETFs found by name" if etf_mode else "")
                   + ". The first search of the day can take a few minutes; after that the results are saved "
                     "for 24 hours.")
    else:
        try:
            with st.spinner(f"Checking the {count} largest {things} in {market}… "
                            "this can take a few minutes the first time each day."):
                rows, screened, when = market_ideas(IDEAS_MARKETS[market], kind)
        except Exception as e:
            rows, screened, when = None, 0, ""
            st.error(f"Couldn't get the list of {things} from Yahoo Finance right now ({e}). Try again later.")

        if rows is not None:
            allowed = {"Tier 1"} | ({"Tier 2"} if include_watch else set())
            if etf_mode:
                keep = [r for r in rows if r["s"]["tier"] in allowed or (include_manual and r["s"]["tier"] == "Incomplete")]
            else:
                keep = [r for r in rows if r["s"]["tier"] in allowed
                        and (r["s"]["business"] == "Pass" or (include_manual and r["s"]["business"] == "Review"))]
            key, desc = sorts[sort_by]
            have = [r for r in keep if r[key] is not None]
            have.sort(key=lambda r: r[key], reverse=desc)
            top = have[:IDEAS_SHOW]
            st.caption(f"{len(keep)} of {screened} {things} passed · screened {when} · source: Yahoo Finance"
                       + (f" · {len(keep) - len(have)} left out because their {sort_by.lower()} figure is missing"
                          if len(have) < len(keep) else ""))
            if not top:
                st.info(f"No {things} passed with these settings. Try including \"on watch\" ones or ones that "
                        + ("can't be fully checked." if etf_mode else "need a manual check."))
            for i, r in enumerate(top, 1):
                d, s = r["d"], r["s"]
                label = TIER_STYLE[s["tier"]][0]
                if s.get("islamic"):
                    label = "Islamic fund"
                elif s["business"] == "Review":
                    label += " · needs manual check"
                figures = [f"{'Size' if etf_mode else 'Value'} {money(r['market_cap'], d['price_ccy'])}" if r["market_cap"] else None,
                           f"Dividend yield {r['yield']:.1%}",
                           f"1-year {r['ret_1y']:+.0%}" if r["ret_1y"] is not None else None,
                           f"Fees {r['fee']:.2%} a year" if r.get("fee") is not None else None,
                           f"P/E {r['pe']:.1f}" if r["pe"] else None,
                           f"Debt {r['debt']:.0%}" if r["debt"] is not None else None]
                with st.expander(f"{i}. {d['name']} ({d['symbol']}) · {label}",
                                 expanded=st.session_state.get("last_opened_ideas") == d["symbol"]):
                    with st.container(key=f"pair_ideas_{kind}_{d['symbol']}"):
                        b1, b2 = st.columns(2)
                        b1.button("Open full check →", key=f"open_ideas_{kind}_{d['symbol']}", type="primary",
                                  on_click=open_check, args=("ideas", d["symbol"], d["name"]), width="stretch")
                        with b2:
                            watch_button(d["symbol"], f"ideas_{kind}", short=True)
                    st.write(" · ".join(f for f in figures if f))
                    if etf_mode:
                        st.write(s["why"])
                        mix_bar(s)
                        if s.get("note"):
                            st.markdown(f'<div class="note">{html.escape(s["note"])}</div>', unsafe_allow_html=True)
                    else:
                        st.write(f"Industry: {d['industry'] or 'unknown'}")
                        if s["business"] == "Review":
                            st.markdown(f'<div class="note">{html.escape(s["why"])}</div>', unsafe_allow_html=True)
                        for name, v in s["ratios"].items():
                            ratio_bar(name, v)
                    source_line(d)
                    research_buttons(d)


with tab_ideas:
    if not navigated("ideas", "Find"):
        ideas_tab()

def add_typed_code():
    code = (st.session_state.get("watch_add") or "").strip().upper()
    if code and code not in watchlist():
        watchlist().append(code)
        save_watchlist()
    st.session_state["watch_add"] = ""


REFRESH_COOLDOWN = 60   # seconds between watchlist refreshes, so Yahoo isn't asked too often


def ask_refresh():
    wait = REFRESH_COOLDOWN - (time.time() - st.session_state.get("watch_refreshed_ts", 0))
    if wait > 0:
        st.session_state["watch_refresh_msg"] = f"Just refreshed. You can refresh again in {wait:.0f} seconds."
    else:
        st.session_state["watch_refresh_pending"] = True


def refresh_watchlist(symbols):
    """Drop the saved data for these stocks/ETFs (and an ETF's top holdings) so they're fetched fresh."""
    to_clear = set()
    for sym in symbols:
        to_clear.add(sym)
        try:
            d = fetch(sym)   # the saved result, to find an ETF's holdings
        except Exception:
            continue
        if d.get("kind") == "fund":
            for ticker, _, _ in d.get("holdings", []):
                to_clear.update(yahoo_candidates(ticker, "", sym))
    for sym in to_clear:
        fetch.clear(sym)
        fetch_failure.clear(sym)
        quote.clear(sym)
    fx_rate.clear()         # fresh exchange rates and ETF provider files too
    download_file.clear()
    st.session_state["watch_refreshed_ts"] = time.time()


def watchlist_tab():
    st.write("A quick overview of the shares and ETFs you're keeping an eye on. Your watchlist is saved in this "
             "page's link, so bookmark it or add it to your home screen.")
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    c1.text_input("Add a share or ETF code", placeholder="e.g. BHP.AX, AAPL, SPY", key="watch_add",
                  on_change=add_typed_code)
    c2.button("Add", on_click=add_typed_code, width="stretch")
    wl = list(watchlist())
    if not wl:
        st.info("Your watchlist is empty. Add a code above, or tap ☆ Add to watchlist on any result in Check or Find.")
        return

    refreshing = st.session_state.pop("watch_refresh_pending", False)
    if refreshing:
        with st.spinner("Fetching fresh data for your watchlist…"):
            refresh_watchlist(wl)
    msg = st.session_state.pop("watch_refresh_msg", None)
    if msg:
        st.toast(msg)
    r1, r2 = st.columns([1, 2], vertical_alignment="center")
    r1.button("↻ Refresh", on_click=ask_refresh, width="stretch",
              help="Fetch fresh prices, company figures and ETF holdings from Yahoo Finance for everything below")
    last = st.session_state.get("watch_refreshed_ts")
    r2.caption(f"Last refreshed {datetime.fromtimestamp(last, APP_TZ):%H:%M %Z}" if last else
               "Results are saved for up to 6 hours (prices 15 minutes). Refresh to fetch the latest now.")

    ctx = get_script_run_ctx()

    def check(sym):
        d, err = get_data(sym)
        return sym, d, err, (evaluate(d) if d else None)

    with st.spinner("Checking your watchlist…"):
        with ThreadPoolExecutor(max_workers=6, initializer=lambda: add_script_run_ctx(None, ctx)) as pool:
            items = list(pool.map(check, wl))
    if refreshing:
        st.toast("Watchlist refreshed with the latest data from Yahoo Finance.")

    counts = {}
    for _, d, _, s in items:
        label = "Couldn't check" if s is None else ("Islamic fund" if s.get("islamic") else TIER_STYLE[s["tier"]][0])
        counts[label] = counts.get(label, 0) + 1
    order = ["Compliant", "Islamic fund", "On watch", "Not compliant", "Can't tell yet", "Couldn't check"]
    tier_of = {"Compliant": "Tier 1", "Islamic fund": "Tier 1", "On watch": "Tier 2", "Not compliant": "Tier 3"}
    st.markdown('<div class="wsum">' + "".join(pill(f"{counts[k]} {k.lower()}", tier_of.get(k, "Incomplete"))
                                               for k in order if k in counts) + "</div>", unsafe_allow_html=True)

    for sym, d, err, s in items:
        if d is None:
            badge = pill("Couldn't check", "Incomplete")
            st.markdown(f'<div class="wcard"><div class="wtop"><b>{html.escape(sym)}</b>{badge}</div>'
                        f'<div class="wsub">{html.escape(err or "")}</div></div>', unsafe_allow_html=True)
            st.button("Remove", key=f"wl_rm_{sym}", on_click=toggle_watch, args=(sym,))
            continue
        status = "Islamic fund" if s.get("islamic") else TIER_STYLE[s["tier"]][0]
        if s["business"] == "Review" and not s.get("islamic"):
            status += " · needs manual check"
        change = ""
        if d.get("prev_close"):
            pct = d["price"] / d["prev_close"] - 1
            change = f' <span class="{"up" if pct >= 0 else "down"}">{pct:+.2%}</span>'
        used, checked, old = based_on(d, s)
        warn = '<div class="wsub down">Some figures are over a year old</div>' if old else ""
        st.markdown(
            f'<div class="wcard"><div class="wtop"><span><b>{html.escape(name_code(d["name"], d["symbol"]))}</b>'
            f'</span>{pill(status, s["tier"])}</div>'
            f'<div class="wprice">{price_text(d["price"], d["price_ccy"])}{change}</div>'
            f'<div class="wsub"><b>Based on:</b> {html.escape(used)}</div>'
            f'<div class="wsub">{html.escape(checked)}</div>{warn}</div>', unsafe_allow_html=True)
        with st.container(key=f"pair_wl_{sym}"):
            b1, b2 = st.columns(2)
            b1.button("Open full check →", key=f"wl_open_{sym}", on_click=open_check, args=("watch", sym, d["name"]),
                      width="stretch")
            b2.button("Remove", key=f"wl_rm_{sym}", on_click=toggle_watch, args=(sym,), width="stretch")


with tab_watch:
    if not navigated("watch", "your watchlist"):
        watchlist_tab()

with tab_mine:
    st.write("Your holdings are saved in this page's link. After making changes, bookmark the page "
             "or add it to your home screen so they're there next time.")
    holdings = read_holdings()
    with st.expander("Edit my holdings", expanded=holdings.empty):
        edited = st.data_editor(
            holdings, num_rows="dynamic", width="stretch", hide_index=True,
            column_config={
                "Code": st.column_config.TextColumn("Code", width=74, help="Share or ETF code, e.g. BHP.AX"),
                "Shares": st.column_config.NumberColumn("Shares", width=70, min_value=0, step=1),
                "Dividend per share (last 12 months)": st.column_config.NumberColumn(
                    "Div.", width=60, min_value=0, format="%.4f",
                    help="Dividend per share over the last 12 months. Leave blank to use Yahoo Finance's figure"),
                "Date it became not compliant": st.column_config.DateColumn(
                    "Red on", width=72, format="D/M/YY", help="Date it became not compliant. Only for stocks that turned red"),
            })
        write_holdings(edited)

    rows = []
    for _, h in edited.iterrows():
        code = str(h["Code"] or "").strip().upper()
        if not code or code == "NONE":
            continue
        d, err = get_data(code)
        if err:
            st.warning(err)
            continue
        s = evaluate(d)
        shares = float(h["Shares"] or 0)
        value = shares * d["price"]
        dps = h["Dividend per share (last 12 months)"]
        if pd.isna(dps):
            dps = d["dps_12m"]
        purge = shares * dps * s["purge_pct"] if dps else 0.0
        zakat = value * ZAKAT_RATE if s["tier"] in ("Tier 1", "Tier 2") else 0.0
        days_left = None
        bd = h["Date it became not compliant"]
        if s["tier"] == "Tier 3" and pd.notna(bd) and bd:
            days_left = (pd.Timestamp(bd).date() - date.today()).days + EXIT_DAYS
        rows.append({"d": d, "s": s, "value": value, "purge": purge, "zakat": zakat, "days_left": days_left})

    if rows:
        reds = [r for r in rows if r["s"]["tier"] == "Tier 3"]
        if reds:
            lines = []
            for r in reds:
                when = (f"{r['days_left']} days left to sell" if r["days_left"] is not None and r["days_left"] >= 0
                        else "selling deadline has passed" if r["days_left"] is not None
                        else "add the date it turned red to see the deadline")
                lines.append(f"- **{name_code(r['d']['name'], r['d']['symbol'])}**: {when}")
            st.error("Not compliant, sell within 60 days:\n" + "\n".join(lines))

        for r in rows:
            label, bg, fg, action = TIER_STYLE[r["s"]["tier"]]
            action = r["s"].get("action") or action
            flag = " · business needs a manual check" if r["s"]["business"] == "Review" else ""
            st.markdown(f"""
            <div class="verdict" style="background:{bg};color:{fg};padding:0.9rem 1.1rem;margin:0.5rem 0">
              <div style="font-size:1.25rem;font-weight:700">{html.escape(name_code(r['d']['name'], r['d']['symbol']))}: {label}</div>
              <div style="font-size:1rem">{action}{flag}</div>
              <div class="who" style="margin-top:0.4rem">Value {money(r['value'], r['d']['price_ccy'])} · price {r['d']['price_date']}</div>
            </div>""", unsafe_allow_html=True)

        st.subheader("Before Ramadan")
        days = (NEXT_REVIEW - date.today()).days
        st.write(f"Yearly review: **{NEXT_REVIEW:%d %B %Y}** (last day of Sha'ban, about {days} days away).")
        totals = {}
        for r in rows:
            rate, ccy = to_display(1, r["d"]["price_ccy"])
            t = totals.setdefault(ccy, {"value": 0, "zakat": 0, "purge": 0})
            t["value"] += r["value"] * rate; t["zakat"] += r["zakat"] * rate; t["purge"] += r["purge"] * rate
        for ccy, t in totals.items():
            st.write(f"**{ccy}**: holdings {money(t['value'], ccy)} · zakat {money(t['zakat'], ccy)} · "
                     f"dividends to give to charity {money(t['purge'], ccy)}")
        st.caption("Zakat is 2.5% of the value of compliant and on-watch holdings. Give dividend cleaning amounts "
                   "to public charity without claiming a tax deduction.")
    elif edited.empty:
        st.info("Add your shares above to see whether each one is still compliant.")

with tab_how:
    st.markdown(f"""
**1. What the business does.** Companies in conventional banking or insurance, gambling, alcohol,
pork or non-halal food, tobacco, adult entertainment or aggressive weapons are excluded.
Less than 5% of revenue may come from non-permissible sources.

The app checks this automatically. It **fails** a company in an excluded industry, or whose interest income
is 5% of revenue or more. It **passes** a company only when its industry isn't a risky one, its company
description mentions nothing non-permissible, and its interest income is under 5%. Anything else, such as
supermarkets that sell alcohol, restaurants, hotels or defence companies, shows **needs a manual check**
until it has been checked by hand and the decision recorded in the app's review list. A recorded decision
always overrides the automatic check.
The automatic check can't see small amounts of non-permissible revenue that a company doesn't describe.

**2. The company's finances.** Three figures are compared with the company's average value over 2 years:
its interest-bearing debt, its cash and interest-earning investments, and the money owed to it.

**3. The result** uses the highest of the three:
- **Compliant** (30% or less): fine to hold and buy.
- **On watch** (30.1–33%): keep your shares, don't add more.
- **Not compliant** (over 33%, or the business fails step 1): don't buy, and sell within {EXIT_DAYS} days.

**4. Cleaning dividends.** Give the non-permissible share of each dividend to public charity.

**5. Yearly review** on the last day of Sha'ban: re-check holdings, give dividend cleaning amounts,
and pay zakat of 2.5% on compliant and on-watch holdings.

**6. ETFs.** The app looks through to the biggest holdings Yahoo Finance publishes and checks each one
with the rules above. An ETF is not compliant if any of them fails, or if more than 1% of it is in bonds.
If they all pass but don't make up the whole fund, it shows "Can't tell yet", because the rest can't be seen.
Funds with Islamic, Shariah or Halal in their name have their own Shariah board, so they're shown as compliant,
with the WattleFolio check of their holdings alongside. Dividend cleaning uses the average of the holdings checked.
Each ETF also shows how much of the fund (by weight) is compliant, needs review, is not compliant, couldn't be
checked, or isn't listed by Yahoo.

**Quick or full ETF check.** Each ETF page lets you choose. **Quick** (the default) checks the top 10 holdings
Yahoo Finance lists: instant, and often enough to see whether a fund holds banks. **Full** checks the fund
provider's complete list. The choice is remembered in the page link and also applies to the Holdings tab.

**Full ETF holdings.** Yahoo Finance only lists an ETF's top 10 holdings. With the full check, when the fund
provider's full holdings file is available (uploaded on the ETF's page, saved in the app and refreshed each night, or downloaded
automatically for SPDR and BetaShares ETFs), the app checks the largest holdings until 95% of the fund is covered, up to 150 holdings. Cash and futures
are shown separately. An ETF checked this way can be compliant if everything checked passes.

**7. Find.** Lists the largest companies in a market that pass the WattleFolio rules, sorted by the
figure you choose. Banks and insurers are left out before screening. Tick **ETFs only** to look through the
largest ETFs instead (plus Islamic ETFs found by name), sorted by size, dividend yield, 1-year change or fees. These are ideas to research, not
recommendations. The list is worked out once a day, so the first search of the day takes a few minutes.

**8. Watchlist.** Add any share or ETF with **☆ Add to watchlist** on its result, **☆ Watch** in Find, or by
typing its code in the Watchlist tab. Each one shows whether it's compliant, its price, and what the result is
based on: the annual report and balance sheet dates (or where an ETF's holdings came from) and when WattleFolio
last checked it. Tap **↻ Refresh** to fetch the latest data for everything on it (once a minute at most).
Your watchlist is saved in the page link.

**How the WattleFolio rules compare with the main standards.** Our rules are closest to Dow Jones Islamic
(same 2-year average), but stricter: on-watch starts at 30%. Each stock's
"More detail" section shows how every standard below would judge it.

""")
    rules_cards()
    st.markdown(f"""
\\* Dow Jones Islamic is reported to have dropped these two tests in September 2023 and now tests debt only.

The app counts all of a company's cash, because Yahoo Finance doesn't separate interest-earning cash.
The standards only count interest-earning cash, so cash-rich companies can look worse here than they really are.

**Prices and currency.** Each result shows the current price and today's change, market value (or fund size
and fees for ETFs), P/E, dividend yield, 52-week range and 1-year change. Use **Show prices in** at the top to
see prices, values, zakat and dividend cleaning in AUD, USD, SGD, MYR and other currencies, converted at Yahoo
Finance exchange rates. Your choice is saved in the page link. The Shariah ratios are the same in any currency.

**Where the figures come from.** Everything comes from Yahoo Finance. Share prices refresh every
15 minutes (the exchange may delay them about 20 minutes). Company figures refresh every 6 hours and use the
newest report Yahoo has: the latest half-year or quarterly balance sheet if it's newer than the annual
report, and revenue from the latest annual report. Debt, cash, money owed and total assets always come from the
same balance sheet (the newest one that reports all of them), and revenue and interest income from the same
year, so no ratio mixes figures from different reports. Each stock shows the dates under its result, and
"More detail" lists exactly which report each figure comes from. The buttons under each result open Yahoo Finance,
the company's annual reports (ASX announcements, SEC 10-K filings or Bursa Malaysia announcements), its website,
and a search for its latest annual report, so you can check the figures yourself.

Figures can be delayed or incomplete. This app is a calculator,
not a fatwa or financial advice. Check with a scholar you trust.
""")
