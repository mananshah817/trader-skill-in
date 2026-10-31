---
name: india-analyst
description: >-
  The Results Analyst for Indian stocks. Reads the quarterly results a
  company filed with NSE (the XBRL file behind each results filing, free,
  no key) and sums up what changed: revenue from operations (total income
  and net interest income for banks and lenders), EBITDA or operating profit,
  net profit, diluted EPS and margins, each quarter against the same quarter
  a year earlier, with the last four quarters added up. Prefers consolidated
  and says which. Finds the latest results announcement (outcome of the
  board meeting, press release, investor presentation) and the next results
  board meeting, and says whether results fall inside the trade. Reads an
  earnings call transcript when you paste one. Use when the user says "how
  were Reliance's results", "analyse the quarter", "what changed this
  quarter", "is revenue growing", "compare margins", "sum up the con call",
  "when are results", "do results fall inside this trade", or before trading
  a stock around its results.
argument-hint: "[NSE symbols]"
---

# india-analyst

Results move Indian stocks more than anything else on the calendar, and the
press release puts the best number first. This skill reads the numbers the
company actually filed with NSE, lines them up quarter by quarter, and says
plainly what changed. For a day trader it answers two questions before a
trade: what did the last results say, and do the next ones fall inside this
trade?

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md
lives). Commands use `python` (the user is on Windows; it is `python3` on
macOS/Linux). Standard library only, no keys.

```bash
python "${CLAUDE_SKILL_DIR}/results.py" --help
```

## What it reads and writes

- NSE's results lists for the symbol:
  `/api/integrated-filing-results?index=equities&type=Integrated%20Filing-%20Financials&symbol=X`
  (every quarter since SEBI's integrated filing began in 2025, one row per
  quarter and basis, Consolidated or Standalone) and the older
  `/api/corporates-financial-results?index=equities&period=Quarterly&symbol=X`
  (to the quarter ended December 2024).
- Each filing's XBRL file on `nsearchives.nseindia.com` (SEBI's in-capmkt
  taxonomy). One file holds one quarter, so the year-ago quarter is its own
  file: the default 5 quarters reads up to 9 files.
- NSE's event calendar (`/api/event-calendar?index=equities&symbol=X`, with
  `/api/corporate-board-meetings` as the fallback) for results board
  meetings, and that day's corporate announcements for the outcome of the
  board meeting, the press release and the investor presentation PDFs.
- `trader/watchlist.json`, `trader/plan.json` and `trader/portfolio.json` for
  the symbols when none are given.
- Writes `trader/metrics.json` with `--metrics`: one row per company (last
  four quarters' top line, net profit and EPS, the latest margins and
  growth). `metrics.example.json` in this folder is a real run for RELIANCE
  and HDFCBANK on 2026-10-31.
- NSE's `results-comparision` API (the quote page's "Financial Results
  Comparison") still stops at December 2024 and is standalone, so the tool
  does not use it.
- NSE sometimes refuses scripts (its bot shield). The tool uses a browser
  User-Agent and Referer, retries once after priming cookies, then says NSE
  refused and to try again in a minute. It never loops.

## The rules

- **Filed numbers first.** The table is what the company filed in XBRL. Use
  the press release and the investor presentation for context (guidance,
  segment colour) and say which numbers came from where.
- **Consolidated first, and say so.** The tool uses consolidated when the
  newest quarter has it (the group, what EPS is built on), else standalone,
  and never mixes the two in one table. `--standalone` forces standalone.
  Net profit is the owners' share (after minority interests) when the filing
  gives it.
- **Compare like with like.** Growth is against the same quarter a year
  earlier. Quarter-on-quarter changes are shown, but many businesses are
  seasonal.
- **Say what is derived.** Indian results have no gross profit or EBITDA
  line. EBITDA is revenue minus expenses other than finance costs and
  depreciation; gross margin is revenue minus materials, purchases and the
  change in inventories. Both are marked derived.
- **Banks and lenders are different.** A bank has no revenue line: total
  income is its top line, net interest income is interest earned minus
  interest expended, and operating profit is "operating profit before
  provisions" as filed. An NBFC's net interest income is interest earned
  minus finance costs. No gross margin or EBITDA for either (blank, not
  zero). Read provisions and bad loans (NPA) in the filing.
- **Bonus issues and splits break EPS.** When the paid-up share capital
  changed against a year earlier, EPS growth is left blank and the tool
  says why. Bonus issues and splits are common in India.
- **Flag one-offs.** Exceptional items inside profit before tax, and other
  income above a quarter of profit before tax, are called out.
- **Say when the data lags.** If the board has met for results but NSE does
  not list the XBRL yet, the tool prints a NOTE: read the outcome of the
  board meeting PDF before summarising.

## Do results fall inside this trade?

The tool reads the next results board meeting from NSE's calendar and
compares it with the trade: today only (a day trade) or up to
`--hold-until YYYY-MM-DD`.

- A meeting inside the trade prints `INSIDE THIS TRADE`. Indian companies
  often release results during market hours on the meeting day (HDFC Bank's
  outcome for the September 2026 quarter reached NSE at 13:56 IST on
  17-Oct-2026), so even a day trade on that date is exposed.
- No meeting on the calendar does not mean no results. SEBI's LODR rules
  (regulation 33) give a company 45 days after a quarter (60 after the
  financial year) and only 2 working days' notice of the board meeting
  (regulation 29). When a quarter has ended and is not reported yet, the
  tool prints the due date and says the date is not known.
- Results that came out just before the trade are flagged too: the price
  may still be reacting.

Say it before `/india-exit` sets the stop: a stop does not protect against a
gap, the stock can open far past it.

## Steps

1. Run it for the companies in question (or the symbols on the watchlist,
   plan and portfolio):

   ```bash
   python "${CLAUDE_SKILL_DIR}/results.py" RELIANCE
   python "${CLAUDE_SKILL_DIR}/results.py" RELIANCE HDFCBANK --metrics trader/metrics.json
   python "${CLAUDE_SKILL_DIR}/results.py" M&M --hold-until 2026-11-06
   ```

   `--quarters 8` shows two years (12 files), `--json` everything
   machine-readable, `--today` pins the trade date, `--no-filings` skips the
   calendar and announcements. Symbols are NSE trading symbols; `RELIANCE.NS`
   works too. Quote `M&M` in the shell.
2. Open the press release, the investor presentation and the outcome PDF
   when the numbers need context, and read them (you can read PDFs).
3. Write the summary: one line on the quarter (top line, net profit, EPS,
   growth), one on margins, one on anything unusual (a one-off, a bonus or
   split, a derived figure, data that lags), whether results fall inside the
   trade, and the links. Plain words, real numbers in rupees crore with
   Indian digit grouping, no adjectives the numbers do not support.

## Earnings call transcripts

The tool does not fetch transcripts. If the user pastes one (or the
company's investor relations page, or its NSE announcements under
"Analysts/Institutional Investor Meet/Con. Call Updates", has it), read it
and sum up what management said changed: guidance, margins, demand, capex,
anything they declined to answer.

## Example: the reel's stock

`results.py RELIANCE` (run 2026-10-31, consolidated): quarter ended
2026-09-30 (Q2 FY27), revenue from operations ₹3,32,861 crore, +28.6%
against a year earlier, net profit ₹25,548 crore (+40.6%), diluted EPS
₹18.88 (+40.7%); EBITDA (derived) 17.0% of revenue, +1.8 points on the
quarter before; net margin 7.7%. Last four quarters: revenue ₹12,12,828
crore, EPS ₹60.68. The Q2 FY26 row has no EPS growth: Reliance's 1:1 bonus
(October 2024) doubled the share count, so EPS a year before it is not
comparable. The board met on 30-Oct-2026 (outcome on NSE at 19:40 IST,
after the close), so a trade on Monday 2-Nov-2026 is the first session
after the results.

On the reel's day, Monday 26-Oct-2026, the meeting was not on NSE's
calendar yet (Reliance gave notice on 27-Oct-2026). The tool would have
said: results for the quarter ended 30-Sep-2026 are due by 14-Nov-2026 and
the date is not known. The day trade, flat by 15:00 IST, was not exposed;
an overnight hold could not know. Once the notice was out, a hold from the
26th to the 30th printed INSIDE THIS TRADE.

`results.py HDFCBANK` (2026-10-31): total income ₹1,29,359 crore (+9.1%),
net interest income ₹44,464 crore (+10.5%), net profit ₹20,700 crore
(+5.6%), diluted EPS ₹13.41 (+5.5%), operating profit before provisions
₹32,872 crore. The tool flags -₹837 crore of exceptional items: read the
notes.

## What it never does

- Never says a stock is cheap, expensive, a buy or a sell. It reports what
  the company filed.
- Never invents a number the filings do not have, or estimates a quarter it
  cannot read.
- Never mixes consolidated and standalone figures in one comparison.
- Never hammers NSE: one request at a time, a pause between them, one retry.

## Safety

Education and tooling, not financial advice. This skill only reads filings.
Nothing places an order except `/india-broker`, after `/india-risk` passes
and the user types an explicit yes for that order.
