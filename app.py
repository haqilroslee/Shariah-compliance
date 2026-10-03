"""
Shariah Stock Checker: a simple web app for family and friends.
Screening rules follow the family methodology (tiers at 30% / 33%, 5% revenue limit,
24-month average market cap, 60-day exit window, purging, 2.5% zakat).
"""
import html
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd
import streamlit as st
import yfinance as yf
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
# "Find stocks" tab: the largest companies in a market are screened, then the best compliant ones listed
IDEAS_MARKETS = {"Australia (ASX)": "au", "United States": "us", "Malaysia (Bursa)": "my"}
IDEAS_CANDIDATES = 100    # how many of the largest companies to screen (more = slower first load)
IDEAS_SHOW = 50
IDEAS_SKIP_SECTORS = ["Financial Services"]   # mostly banks and insurers, so not worth screening
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

TIER_STYLE = {
    "Tier 1": ("Compliant", "#E3F1E8", "#14532D", "Fine to hold and to buy more."),
    "Tier 2": ("On watch", "#FBF0D9", "#7A4B00", "Keep the shares you have, but don't buy more for now."),
    "Tier 3": ("Not compliant", "#F8E1DE", "#8A1C12", f"Don't buy. Sell existing shares within {EXIT_DAYS} days."),
    "Incomplete": ("Can't tell yet", "#ECEEF0", "#33414D", "Some company figures are missing, so this stock can't be scored."),
}

st.set_page_config(page_title="Shariah Stock Checker", page_icon="🌙", layout="centered")
st.markdown("""
<style>
html, body, [class*="css"] { font-size: 18px; }
.verdict { border-radius: 14px; padding: 1.4rem 1.5rem; margin: 0.8rem 0 1.2rem; }
.verdict .label { font-size: 2.3rem; font-weight: 750; line-height: 1.1; letter-spacing: -0.01em; }
.verdict .action { font-size: 1.2rem; margin-top: 0.4rem; }
.verdict .who { font-size: 0.95rem; margin-top: 0.9rem; opacity: 0.8; }
.note { border-left: 4px solid #C98A00; padding: 0.5rem 0.9rem; margin: 0.4rem 0 1rem; }
.bar { position: relative; height: 12px; border-radius: 6px; background: rgba(128,128,128,0.18); margin: 0.25rem 0 0.9rem; }
.bar .fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; }
.bar .tick { position: absolute; top: -4px; bottom: -4px; width: 2px; background: rgba(128,128,128,0.8); }
.row { display: flex; justify-content: space-between; font-size: 1rem; }
.src { font-size: 0.85rem; opacity: 0.8; margin: -0.6rem 0 1rem; line-height: 1.5; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 0.75rem; margin: 0.5rem 0 1rem; }
.card { border: 1px solid rgba(128,128,128,0.3); border-radius: 12px; padding: 0.8rem 1rem; }
.card .head { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; font-weight: 700; }
.card .sub { font-size: 0.85rem; opacity: 0.75; margin: 0.1rem 0 0.4rem; }
.card .line { display: flex; justify-content: space-between; gap: 0.75rem; font-size: 0.95rem; padding: 0.15rem 0; }
.card .line span:last-child { text-align: right; }
.card .line.bad span:last-child { color: #E5484D; font-weight: 700; }
.card small { opacity: 0.7; }
.pill { display: inline-block; border-radius: 999px; padding: 0.1rem 0.6rem; font-size: 0.8rem; font-weight: 600; white-space: nowrap; }
.holding { display: flex; justify-content: space-between; align-items: flex-start; gap: 0.75rem; padding: 0.55rem 0;
           border-bottom: 1px solid rgba(128,128,128,0.2); }
.holding .nm { font-weight: 600; overflow-wrap: anywhere; }
.holding .meta { font-size: 0.85rem; margin-top: 0.2rem; display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: center; }
.holding .wt { font-weight: 600; white-space: nowrap; }
@media (max-width: 640px) {
  html, body, [class*="css"] { font-size: 16px; }
  .verdict { padding: 1.1rem 1.2rem; }
  .verdict .label { font-size: 1.8rem; }
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


class Statement:
    """One financial statement, read newest period first, remembering which periods the figures came from."""
    def __init__(self, df):
        self.df = newest_first(df)
        self.date = self.df.columns[0] if self.df is not None else None
        self.used = set()

    def get(self, *labels):
        if self.df is None:
            return None
        for label in labels:
            if label in self.df.index:
                row = self.df.loc[label].dropna()
                if not row.empty:
                    self.used.add(row.index[0])
                    return float(row.iloc[0])
        return None

    def older(self):
        """Earliest older period a figure had to be taken from, if the newest report was missing some."""
        old = [x for x in self.used if x < self.date]
        return min(old) if old else None


@st.cache_data(ttl=15 * 60, show_spinner=False)
def quote(symbol):
    """Latest price (in the quote's own units) and its date. Refreshed every 15 minutes."""
    closes = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)["Close"].dropna()
    return float(closes.iloc[-1]), closes.index[-1].strftime("%d %b %Y")


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
    closes = t.history(period="1mo", auto_adjust=False)["Close"].dropna()
    if closes.empty:
        raise ValueError("no price data found")
    holdings, assets, sectors = [], {}, {}
    try:
        fd = t.funds_data
        top = fd.top_holdings
        if top is not None and not top.empty:
            holdings = [(str(sym), str(row["Name"]), float(row["Holding Percent"])) for sym, row in top.iterrows()]
        assets = fd.asset_classes or {}
        sectors = fd.sector_weightings or {}
    except Exception:
        pass
    return {
        "kind": "fund",
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName") or symbol,
        "exchange": info.get("exchange", ""),
        "price": float(closes.iloc[-1]) / divisor,
        "price_date": f"{closes.index[-1]:%d %b %Y}",
        "price_ccy": quote_ccy,
        "divisor": divisor,
        "holdings": holdings,
        "bonds": assets.get("bondPosition") or 0.0,
        "sectors": {k: v for k, v in sectors.items() if v},
        "dps_12m": dividends_12m(t, divisor),
    }


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch(symbol):
    t = yf.Ticker(symbol)
    info = t.info or {}
    if info.get("quoteType") in ("ETF", "MUTUALFUND"):
        return fetch_fund(t, info, symbol)
    # Use whichever balance sheet is newer: the annual report or the latest interim (half-year/quarterly) one
    annual, interim = Statement(t.balance_sheet), Statement(t.quarterly_balance_sheet)
    choices = [(st_.date, kind, st_) for kind, st_ in (("annual", annual), ("interim", interim)) if st_.date is not None]
    if not choices:
        raise ValueError("no company figures found")
    _, bs_kind, bs = max(choices, key=lambda c: c[0])   # a tie keeps the annual report
    inc = Statement(t.income_stmt)

    debt = bs.get("Total Debt")
    if debt is not None:
        # "Total Debt" includes leases
        if not INCLUDE_LEASES:
            debt -= bs.get("Capital Lease Obligations") or 0
    else:
        # "Current Debt" and "Long Term Debt" exclude leases
        debt = (bs.get("Current Debt") or 0) + (bs.get("Long Term Debt") or 0)
        if INCLUDE_LEASES:
            debt += bs.get("Capital Lease Obligations") or 0
    cash = bs.get("Cash Cash Equivalents And Short Term Investments")
    if cash is None:
        cash = (bs.get("Cash And Cash Equivalents") or 0) + (bs.get("Other Short Term Investments") or 0)

    quote_ccy = info.get("currency")
    fin_ccy = info.get("financialCurrency") or quote_ccy
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
        shares = bs.get("Ordinary Shares Number", "Share Issued")
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
        "price": float(closes.iloc[-1]) / divisor,
        "price_date": f"end of {closes.index[-1]:%b %Y}",
        "price_ccy": quote_ccy,
        "fin_ccy": fin_ccy,
        "divisor": divisor, "conv": conv, "shares": float(shares),
        "revenue": inc.get("Total Revenue", "Operating Revenue"),
        "interest_income": inc.get("Interest Income", "Interest Income Non Operating"),
        "debt": debt,
        "cash": cash,
        "receivables": bs.get("Accounts Receivable", "Receivables") or 0.0,
        "assets": bs.get("Total Assets"),
        "avg_mcap": float(closes.iloc[-24:].mean()) * shares * conv,
        "avg_mcap_36": float(closes.iloc[-36:].mean()) * shares * conv,
        "dps_12m": dividends_12m(t, divisor),
        "ret_1y": float(closes.iloc[-1] / closes.iloc[-13] - 1) if len(closes) >= 13 else None,
        "mcap": float(closes.iloc[-1]) * shares * conv,
        "as_of": bs.date.strftime("%d %b %Y"),
        "bs_kind": bs_kind,
        "bs_label": (f"{bs.date:%d %b %Y} (FY{bs.date.year} annual report)" if bs_kind == "annual"
                     else f"{bs.date:%d %b %Y} (latest half-year or quarterly report)"),
        "bs_older": bs.older().strftime("%d %b %Y") if bs.older() is not None else None,
        "inc_label": (f"FY{inc.date.year} annual report (year to {inc.date:%d %b %Y})" if inc.date is not None
                      else "not available"),
        "inc_older": inc.older().strftime("%d %b %Y") if inc.older() is not None else None,
        "bs_days": days_old(bs.date),
        "inc_days": days_old(inc.date) if inc.date is not None else 0,
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
    """Family-entered non-permissible revenue %, tolerating '1.2%' or '1,2'. None if blank or unreadable."""
    raw = review["non_permissible_revenue_pct"].strip().rstrip("%").strip().replace(",", ".")
    try:
        return float(raw) / 100 if raw else None
    except ValueError:
        return None


def standards(d, failed_business):
    """Other standards' ratio tests: {name: (basis, [(test, value, limit)], result)}."""
    cash_recv = d["cash"] + d["receivables"] if d["cash"] is not None else None
    djim_tests = [("Debt", ratio(d["debt"], d["avg_mcap"]), DJIM_LIMIT)]
    if DJIM_TEST_CASH_RECEIVABLES:
        djim_tests += [("Cash", ratio(d["cash"], d["avg_mcap"]), DJIM_LIMIT),
                       ("Receivables", ratio(d["receivables"], d["avg_mcap"]), DJIM_LIMIT)]
    out = {
        "AAOIFI": ("current market value", [
            ("Debt", ratio(d["debt"], d["mcap"]), AAOIFI_LIMIT),
            ("Cash", ratio(d["cash"], d["mcap"]), AAOIFI_LIMIT)]),
        "Dow Jones Islamic": ("2-year average market value", djim_tests),
        "S&P Shariah": ("3-year average market value", [
            ("Debt", ratio(d["debt"], d["avg_mcap_36"]), SP_LIMIT),
            ("Cash", ratio(d["cash"], d["avg_mcap_36"]), SP_LIMIT),
            ("Receivables + cash", ratio(cash_recv, d["avg_mcap_36"]), SP_RECEIVABLES_LIMIT)]),
        "MSCI Islamic": ("total assets", [
            ("Debt", ratio(d["debt"], d["assets"]), MSCI_LIMIT),
            ("Cash", ratio(d["cash"], d["assets"]), MSCI_LIMIT),
            ("Receivables + cash", ratio(cash_recv, d["assets"]), MSCI_LIMIT)]),
    }
    results = {}
    for name, (basis, tests) in out.items():
        if failed_business:
            result = "Fail (business)"
        elif any(v is None for _, v, _ in tests):
            result = "n/a"
        else:
            result = "Pass" if all(v <= limit for _, v, limit in tests) else "Fail"
        results[name] = (basis, tests, result)
    return results


FLAG_RE = re.compile(r"\b(" + "|".join(re.escape(w).replace(r"\*", r"\w*") for w in DESCRIPTION_FLAGS) + r")\b",
                     re.IGNORECASE)


def description_flags(text):
    """Distinct flagged words found in a company description, in the order they appear."""
    return list(dict.fromkeys(m.lower() for m in FLAG_RE.findall(text or "")))


def screen(d):
    review = load_review().get(d["symbol"].upper())
    industry = f'{d["industry"]} {d["sector"]}'.lower()

    # Non-permissible revenue: family figure if entered, otherwise interest income as an estimate
    np_pct, np_source = None, ""
    if review is not None and review_pct(review) is not None:
        np_pct, np_source = review_pct(review), "family review"
    elif d["revenue"]:
        np_pct, np_source = min(abs(d["interest_income"] or 0) / d["revenue"], 1.0), "estimate (interest income only)"
        if review is not None and review["non_permissible_revenue_pct"].strip():
            np_source += "; the family figure in sector_review.csv couldn't be read"

    # Almost no sales yet (explorers, biotechs): interest on cash dwarfs revenue, so the 5% test says little
    pre_revenue = np_source.startswith("estimate") and np_pct is not None and np_pct >= PRE_REVENUE_SHARE
    checked_by = "family" if review is not None and review["excluded"].strip().lower() in ("yes", "no") else "automatic"
    if review is not None and review["excluded"].strip().lower() == "yes":
        business, why = "Fail", "The family review marked this business as excluded."
    elif review is None and any(k in industry for k in EXCLUDED_KEYWORDS):
        business, why = "Fail", f"Its industry ({d['industry']}) is on the excluded list."
    elif np_pct is not None and np_pct >= REVENUE_LIMIT and not pre_revenue:
        business, why = "Fail", f"{np_pct:.1%} of revenue comes from non-permissible sources (limit is under 5%)."
    elif checked_by == "family":
        business, why = "Pass", "Business activities checked by the family."
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

    return {
        "tier": tier, "business": business, "checked_by": checked_by, "pre_revenue": pre_revenue, "why": why, "ratios": r, "highest": highest,
        "purge_pct": np_pct or 0.0, "purge_source": np_source,
        "standards": standards(d, business == "Fail"),
    }


def holding_candidates(sym, fund_symbol):
    """Yahoo often lists a fund's local holdings without the exchange suffix (CBA rather than CBA.AX)."""
    if "." not in sym and "." in fund_symbol:
        return [sym + fund_symbol[fund_symbol.rfind("."):], sym]
    return [sym]


def screen_fund(f):
    """Look through an ETF's published top holdings and screen each one with the family rules."""
    islamic = any(w in f["name"].lower() for w in ISLAMIC_FUND_WORDS)
    rows, failed, watch = [], [], False
    checked, purge_sum = 0.0, 0.0
    for sym, name, weight in f["holdings"]:
        d = None
        for cand in holding_candidates(sym, f["symbol"]):
            d, _ = get_data(cand)
            if d is not None and d["kind"] == "stock":
                break
            d = None
        if d is None:
            rows.append({"Holding": name, "Code": sym, "Weight": weight, "Result": "Couldn't check", "tier": "Incomplete"})
            continue
        hs = screen(d)
        result = TIER_STYLE[hs["tier"]][0] + (" (needs manual check)" if hs["business"] == "Review" else "")
        rows.append({"Holding": d["name"], "Code": d["symbol"], "Weight": weight, "Result": result, "tier": hs["tier"]})
        if hs["tier"] == "Incomplete":
            continue
        checked += weight
        purge_sum += weight * hs["purge_pct"]
        if hs["tier"] == "Tier 3":
            failed.append(d["name"])
        watch = watch or hs["tier"] == "Tier 2"

    action, note = None, None
    if islamic:
        tier = "Tier 1"
        action = "An Islamic fund: it is screened by its own Shariah board. Check which standard it follows."
        why = "The fund's name says it is Shariah-compliant, so it follows its own Shariah board's rules."
        if failed:
            note = (f"Under the family rules, {len(failed)} of its top holdings would not be compliant "
                    f"({', '.join(failed)}). Its board may use a different standard.")
    elif f["bonds"] > FUND_MAX_BONDS:
        tier, why = "Tier 3", f"{f['bonds']:.0%} of the fund is in bonds, which pay interest."
    elif failed:
        tier, why = "Tier 3", f"It holds companies that aren't compliant: {', '.join(failed)}."
    elif not f["holdings"]:
        tier, why = "Incomplete", "Yahoo Finance doesn't list this fund's holdings."
        action = "Can't see what this fund holds, so it can't be checked."
    elif checked < FUND_FULL_COVERAGE:
        tier = "Incomplete"
        why = (f"The holdings that could be checked pass, but they're only {checked:.0%} of the fund. "
               f"The other {1 - checked:.0%} can't be seen here.")
        action = "Its top holdings pass, but most of the fund can't be checked here. Ask a scholar or use a certified Islamic ETF."
    else:
        tier = "Tier 2" if watch else "Tier 1"
        why = "Every holding passes the family rules."

    return {
        "tier": tier, "business": "Pass", "why": why, "action": action, "note": note,
        "purge_pct": purge_sum / checked if checked else 0.0,
        "purge_source": f"average of the top holdings checked, {checked:.0%} of the fund" if checked else "",
        "rows": rows, "checked": checked, "islamic": islamic,
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
def market_ideas(region):
    """Screen every company in the market universe. Saved for a day, so only the first visit is slow."""
    universe = market_universe(region)
    ctx = get_script_run_ctx()

    def check(u):
        d, _ = get_data(u["symbol"])
        if d is None or d["kind"] != "stock":
            return None
        s = screen(d)
        return {"d": d, "s": s, "market_cap": u["market_cap"],
                "pe": u["pe"] if u["pe"] and u["pe"] > 0 else None,
                "yield": d["dps_12m"] / d["price"] if d.get("dps_12m") and d["price"] else 0.0,
                "ret_1y": d.get("ret_1y"), "debt": s["ratios"]["Debt"], "highest": s["highest"]}

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
    colour = "#2E7D4F" if value <= TIER1_MAX else "#C98A00" if value <= TIER2_MAX else "#B3261E"
    width = min(value / 0.5, 1) * 100   # bar spans 0-50%
    st.markdown(f"""
    <div class="row"><span>{name}</span><span><b>{value:.1%}</b></span></div>
    <div class="bar"><div class="fill" style="width:{width:.1f}%;background:{colour}"></div>
      <div class="tick" style="left:{TIER1_MAX/0.5*100:.1f}%"></div>
      <div class="tick" style="left:{TIER2_MAX/0.5*100:.1f}%"></div></div>""", unsafe_allow_html=True)


def pill(text, tier):
    _, bg, fg, _ = TIER_STYLE[tier]
    return f'<span class="pill" style="background:{bg};color:{fg}">{html.escape(text)}</span>'


def cards(items):
    """items: [(title, pill_html, subtitle, [(label, value_html, bad)])] -> grid of cards (1 column on phones)."""
    out = []
    for title, badge, sub, lines in items:
        rows = "".join(f'<div class="line{" bad" if bad else ""}"><span>{label}</span><span>{value}</span></div>'
                       for label, value, bad in lines)
        out.append(f'<div class="card"><div class="head"><span>{title}</span>{badge}</div>'
                   f'<div class="sub">{sub}</div>{rows}</div>')
    st.markdown(f'<div class="cards">{"".join(out)}</div>', unsafe_allow_html=True)


def standards_table(s):
    def pct(v):
        return "–" if v is None else f"{v:.1%}"
    cards([(name, pill(result, "Tier 1" if result == "Pass" else "Tier 3" if result.startswith("Fail") else "Incomplete"), f"vs {basis}",
            [(t, f"{pct(v)} <small>/ max {limit * 100:g}%</small>", v is not None and v > limit) for t, v, limit in tests])
           for name, (basis, tests, result) in s["standards"].items()])
    st.caption("For comparison only: the colour above follows the family rules. "
               "Every standard also needs under 5% non-permissible revenue. "
               "\"Cash\" here is all of the company's cash; the standards only count interest-earning cash, "
               "so cash-rich companies can look worse than they really are.")


def fund_detail(d, s):
    st.subheader("Why")
    st.write(s["why"])
    if s["rows"]:
        st.subheader("What's inside")
        st.caption(f"The {len(s['rows'])} biggest holdings Yahoo Finance publishes, each checked with the family rules.")
        st.markdown("".join(
            f'<div class="holding"><div><div class="nm">{html.escape(r["Holding"])}</div>'
            f'<div class="meta"><span>{html.escape(r["Code"])}</span>{pill(r["Result"], r["tier"])}</div></div>'
            f'<div class="wt">{r["Weight"]:.1%}</div></div>' for r in s["rows"]), unsafe_allow_html=True)

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
        st.caption("Only the biggest holdings that Yahoo Finance publishes can be checked. "
                   "For the full list, see the fund's own website.")
        source_detail(d)


def rules_cards():
    """The family rules next to each standard's limits, built from the settings at the top."""
    def lim(x):
        return f"under {x * 100:g}%"
    family = f"{TIER1_MAX * 100:g}% <small>(watch to {TIER2_MAX * 100:g}%)</small>"
    revenue = ("Non-permissible revenue", lim(REVENUE_LIMIT), False)
    djim_extra = "*" if DJIM_TEST_CASH_RECEIVABLES else ""
    cards([
        ("Family rules", "", "vs 2-year average market value",
         [("Debt", family, False), ("Cash", family, False), ("Money owed", family, False), revenue]),
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


def source_links(symbol):
    links = [f"[Yahoo Finance](https://finance.yahoo.com/quote/{symbol}/financials)"]
    if symbol.upper().endswith(".AX"):
        code = symbol.split(".")[0].lower()
        links.append(f"[ASX announcements, incl. annual reports]"
                     f"(https://www.asx.com.au/markets/trade-our-cash-market/announcements.{code})")
    return " · ".join(links)


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
        lines += [f"- **Debt, cash, money owed, total assets:** balance sheet at {d['bs_label']}",
                  f"- **Revenue and interest income:** {d['inc_label']}",
                  f"- **Market value:** month-end share prices for the last 2 and 3 years × "
                  f"{d['shares']:,.0f} shares on issue now; current value uses today's price"]
        if d.get("bs_older"):
            lines.append(f"- Some balance sheet figures were missing from that report, so ones from {d['bs_older']} were used")
        if d.get("inc_older"):
            lines.append(f"- Some income figures were missing from that report, so ones from {d['inc_older']} were used")
    lines.append(f"- **Source:** {source_links(d['symbol'])}. Company figures are refreshed every 6 hours.")
    st.markdown("**Where these figures come from**\n\n" + "\n".join(lines))


def money(x, ccy=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(x) >= div:
            return f"{x/div:,.1f}{suffix} {ccy}".strip()
    return f"{x:,.2f} {ccy}".strip()


def get_data(symbol):
    try:
        d = dict(fetch(symbol))
    except Exception as e:
        return None, f"Couldn't get figures for {symbol} right now ({e}). Check the code, or try again in a few minutes."
    try:
        raw, when = quote(symbol)
        d["price"], d["price_date"] = raw / d["divisor"], when
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
st.title("Shariah Stock Checker")

tab_check, tab_ideas, tab_mine, tab_how = st.tabs(["Check", "Find stocks", "My holdings", "How it works"])

with tab_check:
    query = st.text_input("Company, ETF or share code", placeholder="e.g. Woolworths, BHP.AX, Apple, SPUS")
    if query:
        matches = search(query.strip())
        exact = query.strip().upper()
        if (not matches and "." in exact) or (matches and exact in [m[0] for m in matches]):
            symbol = exact
        elif matches:
            symbol = st.selectbox("Pick the company or ETF", matches, format_func=lambda m: m[1])[0]
        else:
            symbol = None
            st.warning("Nothing found. Try the full name, or the share code (ASX codes end in .AX).")

        if symbol:
            with st.spinner("Checking the figures…"):
                d, err = get_data(symbol)
            if err:
                st.error(err)
            elif d["kind"] == "fund":
                with st.spinner("Checking what the fund holds…"):
                    s = screen_fund(d)
                verdict_card(d, s)
                source_line(d)
                fund_detail(d, s)
            else:
                s = screen(d)
                verdict_card(d, s)
                source_line(d)

                old = ([f"balance sheet at {d['as_of']}"] if d["bs_days"] > STALE_DAYS else []) + \
                      ([f"revenue from the {d['inc_label']}"] if d["inc_days"] > STALE_DAYS else [])
                if old:
                    st.warning(f"Some figures are over a year old ({' and '.join(old)}). Yahoo Finance may be missing "
                               f"the latest reports, so check the company's latest annual report too: "
                               f"{source_links(d['symbol'])}.")

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
                    st.write(f"Business check: **{s['business']}{by}**. {s['why'] if s['business'] != 'Fail' else ''}")
                    st.write(f"Industry: {d['industry'] or 'unknown'} · Reported in {d['fin_ccy']}")
                    st.write(f"Debt {money(d['debt'])} · Cash & investments {money(d['cash'])} · "
                             f"Money owed {money(d['receivables'])} · 2-year average value {money(d['avg_mcap'])} · "
                             f"Total assets {money(d['assets'])}")
                    st.markdown("**How other standards see it**")
                    standards_table(s)
                    source_detail(d)

with tab_ideas:
    st.write(f"The largest companies in a market, screened with the family rules. The top {IDEAS_SHOW} that pass "
             "are listed, sorted the way you choose.")
    st.caption("These are ideas to research, not recommendations or financial advice. They're ranked on figures "
               "only, so check each company yourself before buying.")
    market = st.selectbox("Market", list(IDEAS_MARKETS))
    c1, c2 = st.columns(2)
    sort_by = c1.selectbox("Sort by", list(IDEA_SORTS))
    include_watch = c2.checkbox("Include \"on watch\" stocks", value=False)
    include_manual = c2.checkbox("Include stocks that need a manual business check", value=True)

    if st.session_state.get("ideas_market") != market:
        if st.button(f"Find stocks in {market}", type="primary"):
            st.session_state["ideas_market"] = market
            st.rerun()
        st.caption(f"Checks the {IDEAS_CANDIDATES} largest companies. The first search of the day can take a few "
                   "minutes; after that the results are saved for 24 hours.")
    else:
        region = IDEAS_MARKETS[market]
        try:
            with st.spinner(f"Checking the {IDEAS_CANDIDATES} largest companies in {market}… "
                            "this can take a few minutes the first time each day."):
                rows, screened, when = market_ideas(region)
        except Exception as e:
            rows, screened, when = None, 0, ""
            st.error(f"Couldn't get the list of companies from Yahoo Finance right now ({e}). Try again later.")

        if rows is not None:
            allowed = {"Tier 1"} | ({"Tier 2"} if include_watch else set())
            keep = [r for r in rows if r["s"]["tier"] in allowed
                    and (r["s"]["business"] == "Pass" or (include_manual and r["s"]["business"] == "Review"))]
            key, desc = IDEA_SORTS[sort_by]
            have = [r for r in keep if r[key] is not None]
            have.sort(key=lambda r: r[key], reverse=desc)
            top = have[:IDEAS_SHOW]
            st.caption(f"{len(keep)} of {screened} companies passed · screened {when} · source: Yahoo Finance"
                       + (f" · {len(keep) - len(have)} left out because their {sort_by.lower()} figure is missing"
                          if len(have) < len(keep) else ""))
            if not top:
                st.info("No companies passed with these settings. Try including \"on watch\" stocks or ones that "
                        "need a manual check.")
            for i, r in enumerate(top, 1):
                d, s = r["d"], r["s"]
                label = TIER_STYLE[s["tier"]][0] + (" · needs manual check" if s["business"] == "Review" else "")
                figures = [f"Value {money(r['market_cap'], d['price_ccy'])}" if r["market_cap"] else None,
                           f"Dividend yield {r['yield']:.1%}",
                           f"1-year {r['ret_1y']:+.0%}" if r["ret_1y"] is not None else None,
                           f"P/E {r['pe']:.1f}" if r["pe"] else None,
                           f"Debt {r['debt']:.0%}" if r["debt"] is not None else None]
                with st.expander(f"{i}. {d['name']} ({d['symbol']}) · {label}"):
                    st.write(" · ".join(f for f in figures if f))
                    st.write(f"Industry: {d['industry'] or 'unknown'}")
                    if s["business"] == "Review":
                        st.markdown(f'<div class="note">{html.escape(s["why"])}</div>', unsafe_allow_html=True)
                    for name, v in s["ratios"].items():
                        ratio_bar(name, v)
                    source_line(d)
                    st.caption("For the full check, search for this code in the \"Check\" tab.")

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
                lines.append(f"- **{r['d']['name']}**: {when}")
            st.error("Not compliant, sell within 60 days:\n" + "\n".join(lines))

        for r in rows:
            label, bg, fg, action = TIER_STYLE[r["s"]["tier"]]
            action = r["s"].get("action") or action
            flag = " · business needs a manual check" if r["s"]["business"] == "Review" else ""
            st.markdown(f"""
            <div class="verdict" style="background:{bg};color:{fg};padding:0.9rem 1.1rem;margin:0.5rem 0">
              <div style="font-size:1.25rem;font-weight:700">{html.escape(r['d']['name'])}: {label}</div>
              <div style="font-size:1rem">{action}{flag}</div>
              <div class="who" style="margin-top:0.4rem">Value {money(r['value'], r['d']['price_ccy'])} · price {r['d']['price_date']}</div>
            </div>""", unsafe_allow_html=True)

        st.subheader("Before Ramadan")
        days = (NEXT_REVIEW - date.today()).days
        st.write(f"Yearly review: **{NEXT_REVIEW:%d %B %Y}** (last day of Sha'ban, about {days} days away).")
        totals = {}
        for r in rows:
            t = totals.setdefault(r["d"]["price_ccy"], {"value": 0, "zakat": 0, "purge": 0})
            t["value"] += r["value"]; t["zakat"] += r["zakat"]; t["purge"] += r["purge"]
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
with the family-rule check of their holdings alongside. Dividend cleaning uses the average of the holdings checked.

**7. Find stocks.** Lists the largest companies in a market that pass the family rules, sorted by the
figure you choose. Banks and insurers are left out before screening. These are ideas to research, not
recommendations. The list is worked out once a day, so the first search of the day takes a few minutes.

**How the family rules compare with the main standards.** Our rules are closest to Dow Jones Islamic
(same 2-year average), but stricter: on-watch starts at 30%. Each stock's
"More detail" section shows how every standard below would judge it.

""")
    rules_cards()
    st.markdown(f"""
\\* Dow Jones Islamic is reported to have dropped these two tests in September 2023 and now tests debt only.

The app counts all of a company's cash, because Yahoo Finance doesn't separate interest-earning cash.
The standards only count interest-earning cash, so cash-rich companies can look worse here than they really are.

**Where the figures come from.** Everything comes from Yahoo Finance. Share prices refresh every
15 minutes (the exchange may delay them about 20 minutes). Company figures refresh every 6 hours and use the
newest report Yahoo has: the latest half-year or quarterly balance sheet if it's newer than the annual
report, and revenue from the latest annual report. Each stock shows the dates under its result, and
"More detail" lists exactly which report each figure comes from, with links to check them.

Figures can be delayed or incomplete. This app is a calculator,
not a fatwa or financial advice. Check with a scholar you trust.
""")
