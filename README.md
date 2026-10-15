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
1. Open the app, go to **Holdings**, tap **Edit my holdings** and enter his shares (code + number of shares, plus dividends if known).
2. Open **Share my list** (bottom of the Holdings tab), tick his holdings (and the watchlist if you like) and tap
   **WhatsApp** or **Email** to send him the link.
3. On his phone: open the link, tap **Add to my app**, then **Share → Add to Home Screen**. It now works like an app.
If his holdings change, send him a new link. If he already has the app on his home screen, he copies the link and
pastes it under **Add a list someone shared**: only what he doesn't already have is added.

## Checking a company's business activities
The app checks business activities automatically:
- **Fail:** the industry is excluded (banks, insurance, gambling, alcohol, tobacco and so on), or interest
  income is 5.5% of revenue or more.
- **Needs a manual check:** the industry often mixes permissible and non-permissible sales (supermarkets,
  restaurants, hotels, defence, REITs, packaged food and so on), the company description mentions something
  like alcohol, gambling, pork or lending, figures are missing, or interest income is close to the 5% limit
  (4.5–5.5% of revenue, set by `INCOME_BORDER`). Interest income is only an estimate, so close calls are left to
  the annual report. The app shows it both as a share of revenue and of total income (revenue plus interest, the
  AAOIFI way).
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

Dividends left blank in **Holdings** are filled in from Yahoo Finance (last 12 months).

## ETFs
ETFs can be checked like stocks and added to **Holdings**. The app screens the top holdings Yahoo Finance
publishes (usually the biggest 10). A normal ETF fails if any of them fails or it holds bonds, and shows
"Can't tell yet" if they pass but don't cover the whole fund. Funds with Islamic, Shariah, Sharia, Syariah
or Halal in their name are treated as screened by their own Shariah board (`ISLAMIC_FUND_WORDS` in `app.py`).

## Find
The **Find** tab takes the largest companies in a market (Australia, United States or Malaysia) from
Yahoo Finance's screener, leaves out the Financial Services sector, screens the rest with the WattleFolio rules and
lists the top 50 that pass. You can sort by size, dividend yield, 1-year price change, P/E, debt or room under the
Shariah limits. It only runs when someone taps the button, and results are saved for 24 hours. The number of
companies screened is `IDEAS_CANDIDATES` at the top of `app.py`. Tick **ETFs only** to screen the largest ETFs
(`IDEAS_ETF_CANDIDATES`) plus Islamic ETFs found by name, sorted by size, dividend yield, 1-year change or fees.

Every result has buttons to Yahoo Finance, the company's annual reports (ASX, SEC or Bursa Malaysia), its
website and a Google search for its annual report, for checking the figures by hand.

## Full ETF holdings
Each ETF page has a **Quick / Full** choice. Quick (default) checks Yahoo Finance's top 10 holdings. Full uses the
provider's complete list. The choice is saved in the link (`?etf=full`) and also applies to the Holdings tab; Find
always uses Quick so the market list stays fast. An uploaded file is always used.

Yahoo Finance only lists an ETF's top 10 holdings. With Full, the app uses the provider's full holdings list when it
can: a file uploaded on the ETF's page (for that visit), a file saved in `etf_holdings/` (permanent; see the
README in that folder for Vanguard, BetaShares and SPDR download steps), or, for US SPDR ETFs such as SPY, the
daily file downloaded automatically from State Street (and BetaShares' file for BetaShares ETFs). A GitHub Action
refreshes the funds listed in `etf_holdings/sources.csv` every weekday night; see `etf_holdings/README.md`. It checks the largest holdings until `ETF_FULL_TARGET`
(95%) of the fund is covered, up to `ETF_FULL_MAX` (150) holdings.

## Watchlist
The **Watchlist** tab gives a quick overview of chosen shares and ETFs: status, price and today's change, and what
each result is based on (annual report year and balance sheet date, or the ETF holdings source and date) plus when
WattleFolio checked it, and for companies the Price check zone and when the next results are due (Yahoo's date, or
a rough guess from the reporting cycle: quarterly for US and Bursa companies, half-yearly for others). Add from any
result (**☆ Add to watchlist**), from Find (**☆ Watch**) or by typing a code. **↻ Refresh** fetches fresh data.
The list is saved in the link with the last result seen for each code (`?w=BHP.AX:C,SPY:W`). When a result
changes, its card is highlighted with the old and new result until you tap **OK, got it**.

## Sharing a list
**Share my list** (Watchlist and Holdings tabs) makes a link carrying the watchlist (`?sw=`) and, if ticked, the
holdings (`?sh=`). Opening it shows **Someone shared a list with you** with **Add to my app** / **Not now**; adding
keeps what's already there and skips duplicates. **Add a list someone shared** takes a pasted link instead, for
apps opened from a home-screen icon. Nothing is stored on a server: the list is in the link.

## Holdings
Enter code, number of shares and (optionally) dividends and the date a stock turned red. The **Name** column fills
in from Yahoo Finance to confirm each code ("Code not found" means a typo). Each holding has **Open full check →**.

## Price check
Company results include a **Price check**: the price against an estimated value, the middle of the Graham Number,
Graham's growth formula, a 5-year discounted cash flow and a dividend discount model (whichever have data), with a
margin-of-safety price 25% below. Zones: below the margin-of-safety price, below / near / above estimated value.
Assumptions (`DISCOUNT_RATE`, `TERMINAL_GROWTH`, `GROWTH_CAP`, `GRAHAM_BOND_YIELD`, `MARGIN_OF_SAFETY`,
`NEAR_VALUE_BAND`) are at the top of `app.py`. It's a rough formula-based guide, not a recommendation.

## Saving on your phone and reloading
The watchlist, holdings, currency and ETF setting are kept in the link and also saved on the device (browser
storage), with a time stamp (`?t=`). A home-screen shortcut keeps the link it was saved with, so on opening the app
restores the newer saved version. **↻ Reload app** (top right) reloads the page to pick up the latest version of the
app without losing anything. Each phone keeps its own list; use **Share my list** to copy it to another phone.

## Prices and currency
Each result shows key figures (price and today's change, market value or fund size and fees, P/E, dividend
yield, 52-week range, 1-year change). **Show prices in** at the top converts prices, values, zakat and dividend
cleaning into another currency at Yahoo Finance exchange rates and saves the choice in the link (`?ccy=AUD`),
so a shared link opens in that currency. The list of currencies is `DISPLAY_CURRENCIES` in `app.py`.

## If it stops showing figures
Yahoo Finance sometimes blocks requests from cloud servers. If every stock fails for more than a day,
the fix is switching to a paid data source, which is a small code change.
