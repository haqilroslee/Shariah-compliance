# WattleFolio Shariah Checker: putting it online

Takes about 15 minutes, once. Do this on a computer.

## 1. Put the files on GitHub
1. Create a free account at github.com.
2. Click **+** (top right) → **New repository**. Name it `shariah-checker`, choose **Private**, click **Create repository**.
3. Click **uploading an existing file**. Drag in `app.py`, `requirements.txt` and `sector_review.csv`. Click **Commit changes**.

## 2. Launch the app
1. Go to share.streamlit.io and sign in with your GitHub account.
2. Click **Create app** → deploy from a GitHub repository.
3. Pick the `shariah-checker` repository, branch `main`, main file `app.py`. Optionally choose a custom address, e.g. `wattlefolio-shariah-checker`.
4. Click **Deploy**. The first start takes a few minutes.

## 3. Set up your father-in-law's link
1. Open the app, go to **My holdings**, tap **Edit my holdings** and enter his shares (code + number of shares, plus dividends if known).
2. Copy the full address from the browser bar. His holdings are stored in that link.
3. Send him the link. On his phone: open it, then **Share → Add to Home Screen**. It now works like an app.
If his holdings change, update them in the app and send him the new link.

## Checking a company's business activities
The app checks business activities automatically:
- **Fail:** the industry is excluded (banks, insurance, gambling, alcohol, tobacco and so on), or interest
  income is 5% of revenue or more.
- **Needs a manual check:** the industry often mixes permissible and non-permissible sales (supermarkets,
  restaurants, hotels, defence, REITs, packaged food and so on), the company description mentions something
  like alcohol, gambling, pork or lending, or figures are missing.
- **Pass:** none of the above.

The lists are at the top of `app.py` (`EXCLUDED_KEYWORDS`, `REVIEW_KEYWORDS`, `DESCRIPTION_FLAGS`).
Your own decision in `sector_review.csv` always overrides the automatic check. To record it:
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

## ETFs
ETFs can be checked like stocks and added to **My holdings**. The app screens the top holdings Yahoo Finance
publishes (usually the biggest 10). A normal ETF fails if any of them fails or it holds bonds, and shows
"Can't tell yet" if they pass but don't cover the whole fund. Funds with Islamic, Shariah, Sharia, Syariah
or Halal in their name are treated as screened by their own Shariah board (`ISLAMIC_FUND_WORDS` in `app.py`).

## Find stocks
The **Find stocks** tab takes the largest companies in a market (Australia, United States or Malaysia) from
Yahoo Finance's screener, leaves out the Financial Services sector, screens the rest with the WattleFolio rules and
lists the top 50 that pass. You can sort by size, dividend yield, 1-year price change, P/E, debt or room under the
Shariah limits. It only runs when someone taps the button, and results are saved for 24 hours. The number of
companies screened is `IDEAS_CANDIDATES` at the top of `app.py`. Tick **ETFs only** to screen the largest ETFs
(`IDEAS_ETF_CANDIDATES`) plus Islamic ETFs found by name, sorted by size, dividend yield, 1-year change or fees.

Every result has buttons to Yahoo Finance, the company's annual reports (ASX, SEC or Bursa Malaysia), its
website and a Google search for its annual report, for checking the figures by hand.

## If it stops showing figures
Yahoo Finance sometimes blocks requests from cloud servers. If every stock fails for more than a day,
the fix is switching to a paid data source, which is a small code change.
