"""Reading ETF providers' full holdings files (Vanguard, BetaShares, SPDR and similar layouts).

Shared by the app and by scripts/update_holdings.py, which refreshes the saved files each night.
"""
import csv
import io
import re

import pandas as pd

# Exchange codes used in provider files (Bloomberg style, e.g. "BHP AU") and countries -> Yahoo code endings
EXCHANGE_SUFFIX = {"AU": ".AX", "AT": ".AX", "US": "", "UN": "", "UW": "", "UQ": "", "UP": "", "UA": "", "LN": ".L",
                   "JP": ".T", "JT": ".T", "HK": ".HK", "CN": ".TO", "CT": ".TO", "GR": ".DE", "GY": ".DE", "FP": ".PA",
                   "NA": ".AS", "SW": ".SW", "SE": ".SW", "SS": ".ST", "DC": ".CO", "SM": ".MC", "IM": ".MI", "KS": ".KS",
                   "TT": ".TW", "SP": ".SI", "MK": ".KL", "NZ": ".NZ", "BB": ".BR", "FH": ".HE", "NO": ".OL", "ID": ".IR"}
COUNTRY_SUFFIX = {"australia": ".AX", "united states": "", "united states of america": "", "usa": "", "us": "",
                  "united kingdom": ".L", "uk": ".L", "japan": ".T", "hong kong": ".HK", "canada": ".TO", "germany": ".DE",
                  "france": ".PA", "netherlands": ".AS", "switzerland": ".SW", "sweden": ".ST", "denmark": ".CO",
                  "spain": ".MC", "italy": ".MI", "korea": ".KS", "south korea": ".KS", "korea, republic of": ".KS",
                  "taiwan": ".TW", "singapore": ".SI", "malaysia": ".KL", "new zealand": ".NZ", "belgium": ".BR",
                  "finland": ".HE", "norway": ".OL", "ireland": ".IR"}
TICKER_COLS = {"ticker", "code", "asx code", "symbol", "ticker symbol", "security code", "exchange ticker", "local ticker",
               "bloomberg ticker", "stock code", "ticker code"}
NAME_COLS = {"name", "holding", "holding name", "holdings", "security name", "security", "description",
             "security description", "company", "company name", "issuer name", "issuer", "stock", "asset name"}
CLASS_COLS = {"asset class", "security type", "asset type", "type", "sector", "asset class category", "instrument type"}
NOT_SHARES = re.compile(r"\b(cash|futures?|forwards?|currency|currencies|margin|unsettled|receivables?|payables?|"
                        r"money market|swaps?|options?|derivatives?|fx)\b", re.I)


def _norm(cell):
    return re.sub(r"[^a-z%]+", " ", str(cell).lower()).strip()


def _is_weight_col(n):
    return n in {"weight", "weight %", "weighting", "% weight", "% of net assets", "% net assets", "% of fund",
                 "% of funds", "portfolio weight", "market value %", "% of market value"} or \
        "weight" in n or ("%" in n and any(w in n for w in ("net assets", "fund", "portfolio", "market value")))


def _rows_from_file(data, filename):
    """All rows of a CSV or Excel file as lists of strings (first sheet that has a holdings table)."""
    if filename.lower().endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(io.BytesIO(data), header=None, sheet_name=None, dtype=str)
        return [[("" if pd.isna(c) else str(c)) for c in row] for df in sheets.values() for row in df.itertuples(index=False)]
    text = data.decode("utf-8-sig", errors="replace")
    delim = max([",", ";", "\t"], key=lambda d: text[:5000].count(d))
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def parse_holdings_file(data, filename):
    """Read a provider's full holdings file (Vanguard, BetaShares, SPDR and similar layouts).

    Returns {"entries": [{"ticker", "name", "weight", "country", "cash"}], "as_of": text or ""}.
    Raises ValueError if no holdings table can be found."""
    rows = _rows_from_file(data, filename)
    header_at, cols = None, {}
    for i, row in enumerate(rows[:60]):
        names = [_norm(c) for c in row]
        weight = next((j for j, n in enumerate(names) if _is_weight_col(n)), None)
        ticker = next((j for j, n in enumerate(names) if n in TICKER_COLS), None)
        name = next((j for j, n in enumerate(names) if n in NAME_COLS), None)
        if weight is not None and (ticker is not None or name is not None):
            header_at = i
            cols = {"weight": weight, "ticker": ticker, "name": name,
                    "country": next((j for j, n in enumerate(names) if "country" in n or n == "location"), None),
                    "class": next((j for j, n in enumerate(names) if n in CLASS_COLS), None)}
            break
    if header_at is None:
        raise ValueError("couldn't find a table with holding names and weights in this file")

    def cell(row, key):
        j = cols[key]
        return row[j].strip() if j is not None and j < len(row) else ""

    as_of = ""
    for row in rows[:header_at]:
        cells = [c.strip() for c in row if c and c.strip()]
        if len(cells) >= 2 and _norm(cells[0]) in {"date", "as of date", "as at date", "holdings date", "effective date"}:
            as_of = cells[1]   # e.g. BetaShares: "Date,2026-10-01"
            break
        m = re.search(r"\bas (?:of|at)\b[:\s]*([0-9A-Za-z ,/\-]{6,20})", " ".join(row), re.I)
        if m:
            as_of = m.group(1).strip(" ,")
            break
    entries = []
    for row in rows[header_at + 1:]:
        raw = cell(row, "weight").replace("%", "").replace(",", "").strip()
        if raw.startswith("(") and raw.endswith(")"):
            raw = "-" + raw[1:-1]
        try:
            weight = float(raw)
        except ValueError:
            continue
        ticker, name, klass = cell(row, "ticker"), cell(row, "name"), cell(row, "class")
        if not ticker and not name:
            continue
        cash = bool(NOT_SHARES.search(klass) or (NOT_SHARES.search(name) and not ticker.strip("- ")) or
                    ticker.upper() in {"CASH", "-", "--", "N/A"} or re.search(r"\b(cash|futures?|currency)\b", name, re.I))
        entries.append({"ticker": "" if ticker in {"-", "--"} else ticker, "name": name or ticker, "weight": weight,
                        "country": cell(row, "country"), "cash": cash})
    if not entries:
        raise ValueError("found the table but no holdings with weights")
    total = sum(e["weight"] for e in entries)
    if total > 1.5:   # weights given as percentages
        for e in entries:
            e["weight"] /= 100
    return {"entries": entries, "as_of": as_of}


def yahoo_candidates(ticker, country, fund_symbol):
    """Yahoo codes to try for a holding: handles "BHP AU", "BRK.B", country columns and missing exchange endings."""
    parts = ticker.upper().replace(" EQUITY", "").split()
    if not parts:
        return []
    base, suffix = parts[0], None
    if len(parts) >= 2 and parts[-1] in EXCHANGE_SUFFIX:
        suffix = EXCHANGE_SUFFIX[parts[-1]]
    elif "." in base and base.rsplit(".", 1)[1] in {"AX", "L", "T", "HK", "TO", "DE", "PA", "AS", "SW", "KL", "SI", "NZ"}:
        return [base]   # already a Yahoo code
    if suffix is None and country:
        suffix = COUNTRY_SUFFIX.get(country.strip().lower())
    us = base.replace(".", "-").replace("/", "-")
    if suffix == "":
        return [us]
    if suffix is not None:
        if suffix == ".HK" and base.isdigit():
            base = base.zfill(4)
        return [base.replace("/", "-") + suffix]
    if "." in fund_symbol:   # local fund, local holdings listed without their exchange ending (CBA rather than CBA.AX)
        return [base + fund_symbol[fund_symbol.rfind("."):], us]
    return [us]
