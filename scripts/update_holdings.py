"""Download the full holdings files listed in etf_holdings/sources.csv into etf_holdings/.

Run each night by .github/workflows/update-holdings.yml. A file is only replaced when the download reads as a
proper holdings table, so a failed or broken download keeps the last good file.
"""
import csv
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from holdings import parse_holdings_file  # noqa: E402

FOLDER = os.path.join(ROOT, "etf_holdings")
MIN_HOLDINGS = 5


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (WattleFolio Shariah Checker)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    with open(os.path.join(FOLDER, "sources.csv"), newline="") as f:
        sources = [r for r in csv.DictReader(f) if (r.get("code") or "").strip() and (r.get("url") or "").strip()]
    updated, unchanged, failed = [], [], []
    for row in sources:
        code, url = row["code"].strip(), row["url"].strip()
        ext = ".xlsx" if url.lower().split("?")[0].endswith((".xlsx", ".xls")) else ".csv"
        try:
            data = download(url)
            parsed = parse_holdings_file(data, "download" + ext)
            shares = [e for e in parsed["entries"] if not e["cash"]]
            if len(shares) < MIN_HOLDINGS:
                raise ValueError(f"only {len(shares)} holdings found")
        except Exception as e:
            failed.append(f"{code}: {e}")
            continue
        path = os.path.join(FOLDER, code + ext)
        if os.path.exists(path) and open(path, "rb").read() == data:
            unchanged.append(code)
            continue
        other = os.path.join(FOLDER, code + (".csv" if ext == ".xlsx" else ".xlsx"))
        if os.path.exists(other):
            os.remove(other)
        with open(path, "wb") as f:
            f.write(data)
        updated.append(f"{code} ({len(shares)} holdings{', as of ' + parsed['as_of'] if parsed['as_of'] else ''})")

    print("Updated:", ", ".join(updated) or "none")
    print("Unchanged:", ", ".join(unchanged) or "none")
    if failed:
        print("Failed (kept the previous file):\n  " + "\n  ".join(failed))
    return 1 if failed and not (updated or unchanged) else 0


if __name__ == "__main__":
    sys.exit(main())
