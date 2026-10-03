# Shariah Stock Checker: putting it online

Takes about 15 minutes, once. Do this on a computer.

## 1. Put the files on GitHub
1. Create a free account at github.com.
2. Click **+** (top right) → **New repository**. Name it `shariah-checker`, choose **Private**, click **Create repository**.
3. Click **uploading an existing file**. Drag in `app.py`, `requirements.txt` and `sector_review.csv`. Click **Commit changes**.

## 2. Launch the app
1. Go to share.streamlit.io and sign in with your GitHub account.
2. Click **Create app** → deploy from a GitHub repository.
3. Pick the `shariah-checker` repository, branch `main`, main file `app.py`. Optionally choose a custom address, e.g. `family-shariah-checker`.
4. Click **Deploy**. The first start takes a few minutes.

## 3. Set up your father-in-law's link
1. Open the app, go to **My holdings**, tap **Edit my holdings** and enter his shares (code + number of shares, plus dividends if known).
2. Copy the full address from the browser bar. His holdings are stored in that link.
3. Send him the link. On his phone: open it, then **Share → Add to Home Screen**. It now works like an app.
If his holdings change, update them in the app and send him the new link.

## Checking a company's business activities
The app can't judge business activities on its own. Until you check a company it shows
"Business activities haven't been checked yet". To record your check:
1. On GitHub, open `sector_review.csv` → pencil icon (Edit).
2. Add one line per company, for example:
   `WOW.AX,No,1.2,Checked 2026 annual report`
   - `excluded`: Yes or No
   - `non_permissible_revenue_pct`: % of revenue from haram sources (leave blank to use the interest-income estimate)
   - `note`: anything useful
3. Click **Commit changes**. The app updates itself within a minute or two.

## Changing the rules
All thresholds are at the top of `app.py` (tiers, 5% limit, 60 days, zakat rate, next review date).
Edit them on GitHub and commit. Update `NEXT_REVIEW` each year after Ramadan.

`INCLUDE_LEASES = True` counts lease liabilities as debt (stricter). Set it to `False` to exclude them.
This matters for retailers: Woolworths' debt ratio is about 41% with leases and about 13% without.

The comparison with AAOIFI, Dow Jones Islamic, S&P Shariah and MSCI (under **More detail** on each stock)
is for information only and doesn't change the tier. Dow Jones Islamic reportedly dropped its cash and
receivables tests in September 2023. Once you've confirmed that in the S&P methodology, set
`DJIM_TEST_CASH_RECEIVABLES = False`.

Dividends left blank in **My holdings** are filled in from Yahoo Finance (last 12 months).

## If it stops showing figures
Yahoo Finance sometimes blocks requests from cloud servers. If every stock fails for more than a day,
the fix is switching to a paid data source, which is a small code change.
