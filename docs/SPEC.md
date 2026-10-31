# India Trader skill: the shared contract

This pack is a port of the US Day Trader skill (v2) to the Indian cash market:
NSE equities, intraday (Zerodha MIS), long side only, broker Zerodha Kite.
Every script stays standard-library Python, one file per skill, no pip
installs, and keeps the original's structure, tone and safety rules. This file
is the contract every skill follows, so the eleven scripts agree with each
other. When this file and the original code disagree, this file wins.

Facts checked on 2026-10-31 (sources at the end).

## 1. Names

| US skill | India skill | script |
| --- | --- | --- |
| trader-scanner | india-scanner | scan.py |
| trader-news | india-news | news.py |
| trader-entry | india-entry | entry.py |
| trader-exit | india-exit | exit.py |
| trader-timemachine | india-timemachine | timemachine.py |
| trader-risk | india-risk | check.py |
| trader-budget | india-budget | size.py |
| trader-broker | india-broker | ticket.py |
| trader-alerts | india-alerts | alerts.py |
| trader-analyst | india-analyst | results.py (replaces facts.py) |
| trader-pelosi | india-insider | insider.py (replaces ptr.py) |

- Plugin name `india-trader-skill`, version 1.0.0.
- State still lives in a `trader/` folder in the user's project, same file
  names as the original (watchlist.json, entry.json, plan.json, orders.json,
  prices.json, portfolio.json, config.json, state/*.json).
- `created_by` values are the India skill names (`"india-exit"`, ...).
- Tool User-Agent: `Mozilla/5.0 (compatible; india-trader-skill/1.0)`.
  For www.nseindia.com and nsearchives.nseindia.com use a full browser UA,
  `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36`,
  plus `Referer: https://www.nseindia.com/` and `Accept: application/json, text/plain, */*`.
- SKILL.md commands use `python` (the user is on Windows; say "python3 on
  macOS/Linux" once in each SKILL.md).

## 2. Time and the NSE calendar

- India Standard Time is UTC+5:30 all year, no daylight saving. Replace every
  `eastern_offset / to_eastern / eastern_to_utc` helper with
  `to_ist(utc)` and `ist_to_utc(local)` (fixed 5 h 30 min). Keep naive-UTC
  datetimes internally exactly like the original.
- `--now "2026-10-26 10:00"` (or `10:00` for today) is IST. A trailing `Z` or
  an offset makes it absolute. A trailing ` IST` is accepted and stripped.
  Error text says "an India time like "2026-10-26 10:00"".
- Print times as IST ("10:00 AM IST", "15:00 IST").
- Sessions (normal day):
  - pre-open 09:00 to 09:15 (no trading of our kind; orders queue)
  - continuous trading 09:15 to 15:30 (`OPEN = 09:15`, `CLOSE = 15:30`)
  - Since 3 Aug 2026 stocks that have F&O contracts stop continuous trading
    at 15:15 and go into the Closing Auction Session (CAS) 15:15 to 15:35.
    Stop-loss orders are not allowed in CAS. This is why the default
    `flat_by` is 15:00.
  - Zerodha auto-squares-off MIS at 15:12 for F&O (CAS) stocks and 15:25 for
    the others, charges ₹50 + GST per squared-off order, and blocks fresh MIS
    orders after that time. Mention this wherever flat_by is explained.
  - Yahoo's `.NS` 5-minute bars run 09:15 ... 15:25 (75 slots a day), but
    real data has holes: for F&O stocks the 15:15 / 15:20 / 15:25 bars are
    often empty (the closing auction), and the 09:15 bar's volume is 0 on
    almost every day. A session is complete when it has every bar a tool
    needs up to its own cutoff; never require bars after it, and never
    average volume over a zero-volume 09:15 bar.
- There are no early closes on NSE. Drop the early-close logic.
- Special sessions: `SPECIAL_SESSIONS = {"2026-11-08": {"name": "Muhurat trading (Diwali)", "open": "14:20", "close": "15:30"}}`.
  A special session is a trading session on a weekend. Status and alerts
  show it; india-risk blocks new entries in it unless
  `"allow_special_sessions": true`; scanner/entry/timemachine work on it only
  if asked for that date explicitly.
- Holidays (NSE circular NSE/CMTR/71775 plus the NSE holiday API), weekday
  closures only:

```python
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election (Maharashtra)",
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)
```

- NSE has NOT published 2027 yet (expected mid-December 2026). For a date
  outside CALENDAR_YEARS: weekdays count as trading days, and every tool that
  answers "is the market open" adds one line: "NSE has not published its
  2027 holiday list in this tool yet: check nseindia.com before trading".
  Never invent 2027 holidays.

## 3. Symbols and data

- A symbol is the NSE trading symbol, upper case: letters, digits, `&` and
  `-` (RELIANCE, M&M, BAJAJ-AUTO, 3MINDIA). Regex
  `^[A-Z0-9][A-Z0-9&\-]{0,19}$`. Users may type `RELIANCE.NS`; strip `.NS`.
  A `.BO` suffix means BSE (exchange "BSE").
- Yahoo symbol: `<SYMBOL>.NS` (BSE: `.BO`), URL-quoted (`M%26M.NS`).
  Yahoo chart v8 works for NSE: meta.currency INR, exchangeTimezoneName
  Asia/Kolkata, timestamps are UTC epoch seconds, first bar 03:45 UTC =
  09:15 IST. Keep the original's query2-then-query1 fallback and politeness.
- Universe: `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv`
  (SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ...). Keep SERIES `EQ`
  only (BE / BZ are trade-for-trade: no intraday).
- NSE JSON APIs at `https://www.nseindia.com/api/...` answer with the browser
  UA + Referer above, but Akamai can answer 403 at any time. Every NSE fetch
  gets: one retry after priming cookies (GET https://www.nseindia.com/ with a
  cookie jar, `http.cookiejar` + `urllib.request.build_opener`), then a plain
  error that says NSE refused and the user can try again in a minute. Never
  loop.
- Endpoints found to work on 2026-10-31 (all JSON unless noted):
  - corporate announcements: `/api/corporate-announcements?index=equities&symbol=X&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY` (a plain list; without dates it returns the whole history)
  - quarterly results since 2025: `/api/integrated-filing-results?index=equities&type=Integrated%20Filing-%20Financials&symbol=X` (`{"data": [...]}` with an XBRL link per quarter); the older `/api/corporates-financial-results` stops at Dec 2024
  - board meetings and results dates: `/api/event-calendar?index=equities&symbol=X`, fallback `/api/corporate-board-meetings`
  - bulk / block deals: `bulk.csv`, `block.csv` on nsearchives (latest day), and `/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals|block_deals&from=..&to=..&csv=true` (the JSON form stops at 70 rows a day)
  - insider (SEBI PIT): `/api/corporates-pit-gg?index=equities[&from_date&to_date][&symbol]` for metadata, details in the linked XBRL XML (percentages are fractions); `/api/corporates-pit?symbol=X` is the archive to about Feb 2026
- Price bands: `https://nsearchives.nseindia.com/content/equities/sec_list.csv`
  (Symbol, Series, Security Name, Band, Remarks). Band 2/5/10/20 or
  "No Band" (F&O stocks). Optional; the scanner may flag 2% and 5% band stocks
  as "tight band".

## 4. Tick size and rounding

Replace "round to the cent" with "round to the tick". The NSE tick schedule
(NSE/CMTR/67133, from 15 Apr 2025; reviewed monthly):

```python
def tick_size(price):
    p = float(price)
    if p < 250: return 0.01
    if p <= 1000: return 0.05
    if p <= 5000: return 0.10
    if p <= 10000: return 0.50
    if p <= 20000: return 1.00
    return 5.00
```

- Work in integer paise internally to avoid float drift (like the
  original's integer cents).
- A long stop rounds DOWN to the tick, a target rounds DOWN to the tick, an
  entry limit rounds UP to the tick, a stop-limit price rounds DOWN.
- The tick is taken from the price being rounded. Config may override with
  `"tick_size": {"RELIANCE": 0.1}`.
- Say "the tick schedule is NSE's, reviewed monthly: check Kite's instrument
  list if an order is rejected for its price" once in exit/budget/risk docs.

## 5. Money

- Rupees. Format with Indian digit grouping: `₹1,00,000`, `₹12,34,567.50`,
  `₹46.88`; drop `.00` like the original's `money()`. Negative: `-₹500`.
- On Windows, stdout may not be UTF-8: every `main()` starts with
  `for s in (sys.stdout, sys.stderr): s.reconfigure(encoding="utf-8")`
  (guarded with try/except). Every `open()` of a text file passes
  `encoding="utf-8"`.
- Config keys `*_usd` become `*_inr`.

## 6. Charges (Zerodha equity intraday, checked 2026-10-31)

```python
ZERODHA_INTRADAY = {
    "brokerage_pct": 0.03,      # per executed order...
    "brokerage_max": 20.0,      # ...or ₹20, whichever is lower
    "stt_sell_pct": 0.025,      # sell side only
    "txn_pct": 0.00307,         # NSE transaction charge incl. IPF, both sides (BSE: 0.00375)
    "sebi_pct": 0.0001,         # ₹10 per crore, both sides
    "stamp_buy_pct": 0.003,     # buy side only
    "gst_pct": 18.0,            # on brokerage + transaction charge + SEBI fee
}
```

- `charges(buy_value, sell_value, rates)` returns a breakdown dict and the
  total, rounded to the paisa. india-budget and india-risk share the same
  function (copy it; one file per skill).
- Charges for a round trip are computed at entry value and at the stop value
  (the losing case). With `"include_charges": true` (default) the max loss of
  a trade is `qty x (entry - stop) + charges`, and that is what must fit
  `max_loss_per_trade_inr` and the daily limit. Rates can be overridden in
  config under `"charges"`.
- Auto square-off by Zerodha costs ₹50 + 18% GST per order: say so next to
  flat_by.

## 7. config.json (india-budget/config.example.json)

```json
{
  "budget": 100000,
  "cash_buffer_pct": 10,
  "max_position_pct": 75,
  "max_order_inr": 80000,
  "min_order_inr": 500,
  "max_loss_per_trade_inr": 1000,
  "max_daily_loss_inr": 3000,
  "max_trades_per_day": 3,
  "max_orders_per_day": 10,
  "min_reward_risk": 1.0,
  "require_stop": true,
  "no_entries_before": "09:30",
  "no_entries_after": "14:30",
  "flat_by": "15:00",
  "exchange": "NSE",
  "product": "MIS",
  "entry_limit_buffer_pct": 0.1,
  "stop_limit_buffer_pct": 0.5,
  "include_charges": true,
  "broker": "zerodha",
  "paper": true,
  "blocked": [],
  "allow_margin": false,
  "allow_short": false,
  "allow_special_sessions": false
}
```

Removed: fractional, allow_options (derivatives are always blocked: this pack
is cash equity only), pdt_guard (no PDT rule in India), sec_user_agent.
`allow_margin: false` means a trade is paid in full from cash: MIS leverage is
not used.

## 8. The order contract (trader/orders.json)

Zerodha has no bracket orders (removed 2020) and GTT does not work with MIS.
So one trade is a GROUP of two broker orders plus a target that is watched,
not placed:

1. `role: "entry"`: BUY, MIS, LIMIT at the entry plus
   `entry_limit_buffer_pct` (a marketable limit), rounded UP to the tick.
   LIMIT, not MARKET: Kite requires market protection on market orders and
   the Kite MCP place_order tool cannot send it.
2. `role: "stop"`: SELL, MIS, order_type `SL` (stop-limit), trigger_price =
   the plan stop, price = stop minus `stop_limit_buffer_pct`, rounded DOWN.
   Placed only AFTER the entry is filled (`"after": "entry"`), for the filled
   quantity.
3. The target is in `plan.target`. india-alerts rings at it; the exit is a
   flatten ticket (cancel the stop order, then SELL LIMIT). Never place a
   second resting SELL for the target next to the stop: if both filled the
   account would be short.

```json
{
  "created_by": "india-budget",
  "created_at": "2026-10-26T04:30:00Z",
  "status": "proposed",
  "paper": true,
  "note": "Proposal only. Nothing is placed until india-risk passes it and you say yes in the chat.",
  "orders": [
    {"role": "entry", "group": "RELIANCE-2026-10-26-orb",
     "variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
     "transaction_type": "BUY", "quantity": 62, "product": "MIS",
     "order_type": "LIMIT", "price": 1194.8, "validity": "DAY", "tag": "india-orb",
     "why": "...",
     "plan": {"entry": 1193.6, "limit": 1194.8, "stop": 1183.6, "stop_limit": 1177.6,
              "target": 1203.6, "risk_per_share": 11.2, "charges": 0.0,
              "max_loss": 0.0, "setup": "orb", "entry_time": "10:00", "flat_by": "15:00",
              "date": "2026-10-26"}},
    {"role": "stop", "group": "RELIANCE-2026-10-26-orb", "after": "entry",
     "variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
     "transaction_type": "SELL", "quantity": 62, "product": "MIS",
     "order_type": "SL", "trigger_price": 1183.6, "price": 1177.6, "validity": "DAY",
     "tag": "india-stop", "why": "..."}
  ]
}
```

(The quantity, charges and max_loss above are placeholders; india-budget
computes them.)

- Field names and values are exactly Kite's place_order:
  `KITE_FIELDS = ("variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product", "order_type", "price", "trigger_price", "validity", "validity_ttl", "disclosed_quantity", "tag")`.
  variety regular|co|amo|iceberg|auction (this pack writes `regular` only);
  exchange NSE|BSE; transaction_type BUY|SELL; product MIS (CNC allowed
  only for an exit of a CNC holding); order_type MARKET|LIMIT|SL|SL-M;
  validity DAY|IOC|TTL. quantity is an int, prices are JSON numbers.
  `tag` is at most 20 characters, alphanumeric plus `-`.
- `role`, `group`, `after`, `why`, `plan` are ours and are never sent.
- The broker tool input is the KITE_FIELDS subset, nothing else.
- The max loss is measured at the stop trigger (like the original: "the loss
  at the stop price") plus charges. The SL limit price can fill lower in a
  fast fall; say what the loss would be at the stop-limit price too. A gap
  through the limit leaves the stop unfilled: say that plainly.
- The flatten ticket (`ticket.py flatten SYMBOL`): `role: "exit"`, SELL MIS
  LIMIT at the last price minus `stop_limit_buffer_pct` (rounded DOWN), for
  the held quantity, with the instruction to cancel the open stop order
  first.

## 9. Broker: Zerodha Kite

- `"paper": true` is the default. Zerodha has no paper account, so paper
  mode never calls any Kite tool that places, modifies or cancels anything:
  the ticket is approved, then recorded as a paper fill with
  `ticket.py fill ... --paper` at a price the user or prices.json gives, and
  trader/portfolio.json is updated. Read-only Kite tools (get_ltp,
  get_quotes) are fine in paper mode.
- Live mode: the official Kite MCP server (github.com/zerodha/kite-mcp-server).
  Tools: login, get_quotes, get_ltp, get_ohlc, get_historical_data,
  search_instruments, get_profile, get_margins, get_holdings, get_positions,
  get_mf_holdings, place_order, modify_order, cancel_order, get_orders,
  get_trades, get_order_history, get_order_trades, get_gtts,
  place_gtt_order, modify_gtt_order, delete_gtt_order.
- The hosted server (https://mcp.kite.trade/mcp) excludes order placement
  (as of Zerodha's launch post, May 2025; could not confirm a change). Live
  orders need the self-hosted server with your own KITE_API_KEY /
  KITE_API_SECRET in environment variables, never in chat.
- Since 1 Apr 2026, Kite API order placement / modify / cancel needs a
  static IP registered with Zerodha (up to 2). The free "Personal" Kite
  Connect plan includes order APIs; the ₹500/month "Connect" plan adds live
  and historical data.
- SEBI's retail algo framework (binding from 1 Apr 2026): orders from your
  own API key stay under the 10 orders/second threshold; this pack places one
  order at a time, after a typed yes.
- Claude Desktop config example (hosted, read-only):
  `{"mcpServers": {"kite": {"command": "npx", "args": ["mcp-remote", "https://mcp.kite.trade/mcp"]}}}`
- Set place_order, modify_order, cancel_order, place_gtt_order,
  modify_gtt_order, delete_gtt_order to "Needs approval" in Claude, never
  "Always allow".
- The three gates stay: india-risk PASS for this exact order, the user's
  typed yes for this exact group (the yes covers the entry and its
  protective stop; the ticket shows both), and Claude's own Allow once /
  Deny card for each place_order call.

## 10. Safety copy (every order-path SKILL.md)

Keep the original's rules and wording style, adapted:
- "Not financial advice." This is software that reads public data and does
  arithmetic.
- SEBI's study of FY23: about 7 in 10 (71%) individual intraday traders in
  the cash segment made a loss.
- No trade goes through until you say yes. Paper first.
- No F&O, no short selling, no margin unless turned on (F&O is never on in
  this pack).

## 11. Tests

- Offline unittest, standard library, one file per skill:
  `tests/test_<skill>.py` loading the script with `tests/_load.py`.
- Fixtures under `tests/fixtures/india-<skill>/`, cut from REAL data fetched
  on 2026-10-31 where possible (Yahoo .NS charts, NSE CSV/JSON), synthetic
  only where real data is impossible; say which in a comment.
- Delete the US fixtures and US tests you replace.
- The reel (the worked example every skill reproduces): RELIANCE, Monday
  26 October 2026, 5-minute bars.
  - opening range 09:15 to 09:30: low 1175.30, high 1192.00
  - the 09:55 bar closes at 10:00 IST at 1193.60, the first close above
    the range: the entry, 10:00
  - stop at the range midpoint 1183.65, rounded down to the 0.10 tick:
    1183.60; risk 10.00 a share
  - target 1R: 1203.60; flat by 15:00
  - raw data: Yahoo's RELIANCE.NS chart, range=60d interval=5m, fetched on
    2026-10-31 (60 days of 5-minute bars to 2026-10-30). A fixture "as
    fetched at 10:00 IST on 2026-10-26" must keep only bars that had started
    before then (tests/fixtures/india-timemachine/RELIANCE-60d5m.json.gz).
- Budget numbers for the reel come from india-budget with the
  config.example.json above; other skills' fixtures that need them use
  india-budget's output.

## Sources

- NSE holidays 2026: https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf
- Muhurat 2026: https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf
- CAS (SEBI, 16 Jan 2026): https://www.sebi.gov.in/sebi_data/attachdocs/jan-2026/1768576287344.pdf
- Zerodha auto square-off: https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings
- Zerodha charges: https://zerodha.com/charges/
- Tick sizes: https://nsearchives.nseindia.com/content/circulars/CMTR67133.pdf
- Kite Connect orders: https://kite.trade/docs/connect/v3/orders/
- Kite static IP: https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip
- Kite MCP: https://github.com/zerodha/kite-mcp-server
- No bracket orders: https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/why-bo-stopped
- GTT products: https://support.zerodha.com/category/trading-and-markets/gtt/articles/what-is-the-good-till-triggered-gtt-feature
- SEBI retail algo: https://www.sebi.gov.in/sebi_data/attachdocs/feb-2025/1738665456458.pdf and https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf
- SEBI intraday study (FY23, 24 Jul 2024): reported by Business Standard
