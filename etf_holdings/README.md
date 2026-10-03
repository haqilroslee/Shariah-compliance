# Full ETF holdings files

Yahoo Finance only lists an ETF's top 10 holdings. Put the fund provider's full holdings file here and the app
uses it instead, checking the largest holdings until 95% of the fund is covered (up to 150 holdings).

**Name the file after the fund's code**, as used in the app: `VAS.AX.csv`, `A200.AX.csv`, `VGS.AX.xlsx`
(`VAS.csv` also works). CSV and Excel files are both fine. Don't edit the file; upload it as downloaded.

| Provider | Where to download |
|---|---|
| Vanguard | The ETF's page on vanguard.com.au → *Portfolio* → export the holdings list |
| BetaShares | The fund's page on betashares.com.au → *Holdings* → download the full holdings (CSV) |
| SPDR (State Street) | Downloaded automatically for US SPDR ETFs such as SPY. Manual: ssga.com fund page → *Holdings* → *Daily holdings* |

Holdings of index ETFs change slowly, so refreshing the file every month or quarter is enough. The app shows the
file's date under "What's inside".

To add or replace a file on GitHub: open this folder → **Add file → Upload files** → drag the file in →
**Commit changes**. The app picks it up within a few minutes.
