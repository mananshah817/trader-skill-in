---
name: india-insider
description: >-
  The Insider and Big Money Tracker for Indian stocks. Reads two kinds of
  public NSE disclosure: bulk and block deals (which fund, promoter or
  trader bought or sold a big block of a stock, how many shares, at what
  price, worth how many crore) and insider trading disclosures under SEBI's
  PIT Regulations 2015 (promoters, directors, key managerial personnel and
  designated persons reporting their own trades: who, what category, how
  many shares, value, buy or sell, market trade or not, trade date and how
  many trading days it took to reach NSE). Alerts once per new deal or
  filing and flags rows in stocks on your watchlist, plan or portfolio. Use
  when the user says "who bought Swiggy", "bulk deals today", "block
  deals", "is the promoter buying", "insider trading in X", "promoter
  selling", "did any mutual fund buy X", "alert me when a promoter buys",
  or "run the insider skill".
argument-hint: "[symbol] [--read]"
---

# india-insider

Big investors and company insiders in India have to disclose their trades.
NSE publishes every bulk and block deal at the end of the day, and every
insider trading disclosure a company files under SEBI's Prohibition of
Insider Trading (PIT) Regulations 2015. This skill reads both, notices what
is new since the last check, and reads each filing for you.

It is fast. It is not early. A bulk or block deal is published after the
close of the day it happened. An insider disclosure is due from the person
to the company within 2 trading days of the trade, and from the company to
the exchange within 2 trading days after that (PIT Regulations, regulation
7(2)), so by the time anyone reads it the trade is days old. There is no
inside information here: only public filings, read quickly. Say that every
time. Following promoters or big investors is not a strategy guarantee.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md
lives). Commands use `python` (the user is on Windows; it is `python3` on
macOS/Linux). Standard library only, no keys.

```bash
python "${CLAUDE_SKILL_DIR}/insider.py" --help
```

## What it reads and writes

- Bulk and block deals, the latest day:
  `https://nsearchives.nseindia.com/content/equities/bulk.csv` and
  `block.csv` (Date, Symbol, Security Name, Client Name, Buy/Sell, Quantity
  Traded, Trade Price / Wght. Avg. Price).
- A range of days: NSE's
  `/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=DD-MM-YYYY&to=DD-MM-YYYY&csv=true`
  (and `block_deals`). The tool asks for the CSV form: the JSON form stops at
  70 rows a day.
- Insider filings: `/api/corporates-pit-gg?index=equities&from_date=..&to_date=..[&symbol=X]`.
  Each row is one filing (company, time, regulation) with an XML file. Who,
  what and how much are inside the XML (`--read`): name, category
  (Promoter, Promoter Group, Director, KMP, Designated Person, ...),
  instrument, number of securities, value, transaction type (Buy, Sell,
  Pledge, ...), mode (Market Purchase, Allotment, ESOP, Off Market, ...),
  trade dates, the date the company was told, and the holding before and
  after.
- NSE's older list, `/api/corporates-pit?index=equities&symbol=X`, has the
  details in each row but stops in February 2026 (`--archive`).
- Writes `trader/state/insider.json` (what `check` has shown, kept 30 days,
  so each item alerts once).
- Reads `trader/watchlist.json`, `trader/plan.json` and
  `trader/portfolio.json` (and `--tickers`) to flag rows in your stocks.
- NSE sometimes refuses scripts (its bot shield). The tool uses a browser
  User-Agent and Referer, retries once after priming cookies, then says NSE
  refused and to try again in a minute. It never loops.

## The rules

- **Quote the filing, not a tracker.** Every row comes from NSE's file or
  the company's XML, with its link. The filing wins over any app.
- **A bulk deal is one client trading more than 0.5% of the company's
  shares in a day; a block deal is a large negotiated trade in NSE's
  separate block-deal window** (a minimum order of ₹25 crore under SEBI's
  2023 rules; one seller can be matched by many buyers, so single rows can
  be small). A block deal that is also over 0.5% shows up in both lists:
  the tool keeps it once and says so.
- **Churn is not a position.** A client that bought and sold the same stock
  on the same day (high-frequency firms and brokers' own books) is marked
  "bought and sold the same day".
- **Only market trades are trades.** "Market Purchase" and "Market Sale" are
  someone buying or selling at the screen price. Allotments (a preferential
  issue, warrants), ESOPs, gifts, inheritance, off-market transfers and
  pledges are not, and the tool says "not a market trade". A promoter's
  pledge being invoked is a lender selling, not the promoter choosing to.
- **Say the delay.** Each insider row shows how many trading days passed
  between the trade and NSE publishing it; over 4 is marked SLOW. (The
  2-day rule applies when a person's trades in a calendar quarter pass ₹10
  lakh.)
- **Say when a value looks wrong.** Filings have typos (a per-share price in
  the value box). The tool flags a value under ₹1 a share.
- **Some trades are not in this list.** Some insider trades now reach the
  exchanges through depository "system-driven disclosures" instead of a
  company filing (a Reliance filing of May 2026 says it is filed because the
  transaction "is presently not covered in the System Driven Disclosure").
  If a trade you expect is missing, look at NSE's Insider Trading and SAST
  pages in a browser.

## Step 1: bulk and block deals

```bash
python "${CLAUDE_SKILL_DIR}/insider.py" deals                              # NSE's latest day
python "${CLAUDE_SKILL_DIR}/insider.py" deals --min-crore 100
python "${CLAUDE_SKILL_DIR}/insider.py" deals --days 7 --symbol SWIGGY
python "${CLAUDE_SKILL_DIR}/insider.py" deals --days 30 --client "mutual fund" --side buy
python "${CLAUDE_SKILL_DIR}/insider.py" deals --kind block --mine
```

`--days N` is calendar days back including today (1 is NSE's latest files).
`--symbol` repeats; `--mine` keeps your watchlist, plan and portfolio only.

## Step 2: insider (PIT) disclosures

```bash
python "${CLAUDE_SKILL_DIR}/insider.py" insider                            # last 3 days, the list
python "${CLAUDE_SKILL_DIR}/insider.py" insider --symbol NATCOPHARM --days 30 --read
python "${CLAUDE_SKILL_DIR}/insider.py" insider --category promoter --market-only --days 2
python "${CLAUDE_SKILL_DIR}/insider.py" insider --symbol RELIANCE --archive
python "${CLAUDE_SKILL_DIR}/insider.py" read <xml file or link>
```

`--category`, `--side buy|sell` and `--market-only` read each filing's XML
(at most `--limit 25` per run: narrow with `--symbol` or `--days`).
`--category promoter` matches Promoter, Promoter Group and Promoter and
Director.

## Step 3: watch for new ones

```bash
python "${CLAUDE_SKILL_DIR}/insider.py" check --mine --read
python "${CLAUDE_SKILL_DIR}/insider.py" check --category promoter --kind pit
```

Shows only deals and filings not seen at the last check, flags your stocks
first, and records them in the state file. Exit code 1 means nothing new.
`--dry-run` leaves the state alone. Deals appear once a day after the
close; insider filings arrive all day (and on Saturdays). Checking every
hour on trading days is plenty: `/loop 1h` or a scheduled task.

## Example: real rows

`deals --min-crore 200` (run 2026-10-31, NSE's file for 30-Oct-2026):

```
30-Oct-2026  BLOCK SWIGGY  SELL TENCENT CLOUD EUROPE B.V.  5,43,47,662 @ ₹180.65 = ₹981.79 crore
             (also a bulk deal: one trade, over 0.5% of the company)
30-Oct-2026  BLOCK SWIGGY  BUY  SOCIETE GENERALE           2,37,40,000 @ ₹180.65 = ₹428.86 crore
```

Then: "Tencent sold about ₹982 crore of Swiggy in the block window on
Friday, bought by Societe Generale, Morgan Stanley, Goldman Sachs and
mutual funds. This was published after Friday's close. It says what
happened on Friday, not what the price will do on Monday."

`insider --symbol WESTLIFE --days 3 --read` (29-Oct-2026): "promoter
Horizon Impex Private Limited: market purchase of 10,65,000 shares, ₹63.90
crore (about ₹599.99 a share), traded 27-Oct-2026 to 28-Oct-2026; holding
30.49% to 31.18%; on NSE 1 trading day later." NATCOPHARM's filing the same
day lists 25 people, every row an allotment of new shares at ₹750,
not a market purchase.

If the user wants to act on it, it goes the long way: a disclosure is old
news, so it is a reason to look at a chart, not a setup. `/india-scanner`
and `/india-entry` decide if there is a trade today; then cash equity only,
sized by `/india-budget`, checked by `/india-risk`, placed by
`/india-broker` only after the user's yes.

## What it never does

- Never says or implies anyone has inside information, or that copying a
  promoter or a fund beats the market. No return claims.
- Never presents an allotment, ESOP, gift or pledge as a purchase or sale.
- Never places an order, and never trades F&O on anything it reads.
- Never logs in anywhere and never hammers NSE: one request at a time, a
  pause between them, one retry.

## Safety

- Education and tooling, not financial advice. Nothing in this pack is a
  recommendation to buy or sell anything.
- Paper trading by default (`"paper": true` in `trader/config.json`).
- Nothing places an order except `/india-broker`, and only after
  `/india-risk` passes for that exact order AND the user types an explicit
  yes for it in the chat, AND the Kite order tools are set to "Needs
  approval" in Claude so Claude's own "Allow once / Deny" card appears too.
  Never "Always allow" an order tool.
- No F&O, no short selling, no margin unless the config turns it on (F&O is
  never on in this pack). SEBI's study of FY23: about 7 in 10 individual
  intraday traders in the cash segment made a loss.
- Bulk, block and insider disclosures are PUBLIC and LATE (end of day, and
  up to 2 + 2 trading days for insider filings). This skill reads them
  fast; it has no inside information, and it says so.
