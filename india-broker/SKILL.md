---
name: india-broker
description: >-
  Order Executor. Sends approved trades to Zerodha Kite, paper mode by
  default, never without your yes. Shows each trade as a plain ticket in
  rupees: the entry (BUY, MIS, LIMIT), its protective stop (SELL, SL, placed
  only after the entry fills), the target that is watched and not placed,
  and the max loss at the stop with charges, plus the exact input for
  Kite's place_order tool. Records your yes word for word (one yes covers
  one trade: the entry and its stop), then Claude's own Allow once / Deny
  card asks again for each place_order call. Paper mode never calls a Kite
  tool that places, modifies or cancels: it records paper fills and keeps
  trader/portfolio.json. Builds the exit ticket that closes a trade by your
  flat-by time (cancel the stop first, then SELL LIMIT), records fills, and
  logs each trade's max loss so india-risk can hold the daily limit. Covers
  the hosted read-only Kite MCP server, the self-hosted one needed for live
  orders (your own API key in env vars, never in chat, a registered static
  IP), and setting order tools to "Needs approval". Use when the user says
  "place the order", "buy it", "execute", "send it to Zerodha", "close my
  position", "flatten", "get me out", "connect Kite", "set up paper
  trading", or after /india-risk passes.
argument-hint: "[ticket number]"
---

# india-broker

The Order Executor is the only skill in this pack that can lead to a real order, so it is the
strictest. An order reaches the market only after three separate gates, every time:

1. **india-risk passed these exact orders today** (matched by fingerprint).
2. **The user typed an explicit yes for this one trade** in the chat. The yes covers the entry and
   its protective stop; the ticket shows both.
3. **The user clicked Allow once** on Claude's own permission card, for each place_order call, which
   appears because the order tools are set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order. Paper first.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives). The commands use
`python` (Windows); on macOS/Linux use `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python "${CLAUDE_SKILL_DIR}/ticket.py" fill --index 1 --paper --price 1194.20      # paper mode
python "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --leg entry --status placed --order-id ID   # live mode
python "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 62 --price 1199.40
python "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply is a real yes for that
trade, records paper fills, builds the flatten ticket, and keeps the log in `trader/state/broker.json`.
`--now "2026-10-26 10:00"` pins the clock (India time, IST).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (india-budget writes it; `flatten` replaces it; a partial fill rewrites the stop's quantity) |
| `trader/state/risk.json` | india-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed, cancelled and filled, with each entry's max loss (paper and live) |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` (before charges: Zerodha's contract note has the exact charges) |
| `trader/prices.json` | a fresh price for a paper fill or a flatten, if the user gives none |
| `trader/plan.json` | the flat-by time and exchange for `flatten` |

## Paper mode (the default)

`"paper": true` in `trader/config.json`. Zerodha has no paper account, so in paper mode Claude **never
calls place_order, modify_order, cancel_order or the GTT tools**. Read-only Kite tools (get_ltp,
get_quotes) are fine. After the user's yes, the trade is recorded as a paper fill at a real price, the
user's or a fresh one from `trader/prices.json`:

```bash
python "${CLAUDE_SKILL_DIR}/ticket.py" fill --index 1 --paper --price 1194.20
```

A paper LIMIT BUY does not fill above its limit, and a paper stop does not fill above its trigger or
below its own limit (the gap case: you still hold the shares). After the entry's paper fill the stop is
recorded as resting; when india-alerts rings at the stop, record it with `fill --index 1 --leg stop
--paper --price P`. The paper flatten cancels the paper stop first. Every paper fill updates
`trader/portfolio.json`, and india-risk counts paper trades in the daily limits exactly like live ones.

## Setup A: the hosted Kite MCP server (read only)

Zerodha's hosted server needs no keys: you log in to Kite in the browser. In Claude Desktop:
Settings > Developer > Edit Config opens `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\`,
macOS: `~/Library/Application Support/Claude/`). Add this block (also in
`claude_desktop_config.example.json` here), with Node.js installed for `npx`:

```json
{"mcpServers": {"kite": {"command": "npx", "args": ["mcp-remote", "https://mcp.kite.trade/mcp"]}}}
```

Restart Claude Desktop, then ask Claude to use the Kite `login` tool and finish the login on
kite.zerodha.com yourself. The hosted server leaves out order placement (as of Zerodha's launch post,
May 2025; this pack could not confirm a change). Use it for quotes, holdings and positions, and for
paper mode.

## Setup B: live orders (self-hosted Kite MCP server)

Live orders need the open-source server, github.com/zerodha/kite-mcp-server, running on your own
computer with your own Kite Connect app. Follow that README for the build and run steps.

1. Create a Kite Connect app at developers.kite.trade. The free "Personal" plan includes the order APIs;
   the ₹500/month "Connect" plan adds live and historical market data.
2. Since 1 Apr 2026 Kite's API only places, modifies and cancels orders from a static IP you have
   registered with Zerodha (up to 2). Register it in the app before going live.
3. Put `KITE_API_KEY` and `KITE_API_SECRET` in the server's **environment variables**. **Keys never go
   in the chat.** Never ask for them, never read the config back into the conversation, and if the
   user pastes a key or secret anyway, tell them to delete that message and regenerate the secret in
   the Kite Connect console.
4. Point Claude at the local server the same way as Setup A (the server's README gives the URL).
5. **Set the tool permissions.** Customize > Connectors (Settings > Connectors in older versions), open
   kite, and set `place_order`, `modify_order`, `cancel_order`, `place_gtt_order`, `modify_gtt_order`
   and `delete_gtt_order` to **Needs approval**. Never Always allow. Read-only tools (`get_ltp`,
   `get_quotes`, `get_ohlc`, `get_positions`, `get_orders`, `get_trades`, `get_margins`,
   `get_holdings`, `get_profile`) can be Always allow. In Claude Code keep them on ask in
   `.claude/settings.json`: `"permissions": {"ask": ["mcp__kite__place_order", "mcp__kite__modify_order",
   "mcp__kite__cancel_order"]}`, and never put an order tool in `allow`.
6. Set `"paper": false` yourself, in your own config. Claude never does it.

SEBI's retail algo framework (binding from 1 Apr 2026): orders from your own API key must stay under
10 orders a second. This pack places one order at a time, after a typed yes.

**Why two orders, not a bracket.** Zerodha removed bracket orders in 2020 and GTT does not work with
MIS. So a trade is an entry LIMIT plus an SL stop placed after the entry fills. The target is not a
resting order: on Kite an open SL SELL and a second SELL for the target could both fill and leave you
short. Both orders are DAY orders and MIS: one more reason to be flat by `flat_by`.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT READY or BLOCKED goes back
   to `/india-risk`.
2. Show ONE ticket and ask: "Take this trade? Reply yes or no." One ticket per question. Never offer
   "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`. If it prints "Not
   recorded", the reply was not an explicit yes for this trade (for example "sure", "haan", "yes to
   all", or a yes that names a different stock or quantity). Ask again. Never turn a reply into a yes
   yourself.
4. **Paper**: `ticket.py fill --index N --paper --price P`. No Kite order tool is called.
5. **Live**: only after "Recorded your yes", call `place_order` with exactly the entry JSON it printed.
   Claude's card appears: Allow once or Deny. Record it: `ticket.py result --index N --leg entry
   --status placed --order-id <id>` (or `--status denied`, or `--status failed --note ...`). A failed
   order is not retried without a new check and a new yes.
6. **Live, the stop**: only after the entry fill is confirmed with `get_orders` (status COMPLETE) and
   `get_positions` (the shares are there), record the fill: `ticket.py fill --index N --qty Q --price P`.
   It prints the stop's place_order input for the filled quantity. Call `place_order` with exactly that
   (Allow once), then `ticket.py result --index N --leg stop --status placed --order-id <id>`.
   `result` refuses a stop before the entry fill is recorded.
7. **A partial fill**: the stop must be for the shares you hold, never more. `fill` rewrites the stop leg
   in `trader/orders.json` to the filled quantity; that is a new order, so run `/india-risk` again and
   ask the user's yes for the stop. Live: the rest of the entry may still be open at Kite: cancel it
   (`cancel_order`, Needs approval) or it can fill later without a stop.

The log keeps each entry's max loss (charges included, from india-risk) for the daily limit.

## Steps: while it is open, and out by flat_by

- india-alerts watches the stop, the target and the flat-by time (`alerts.py add --from-plan`). The SL
  order at Kite does the stopping; a gap below its limit leaves it unfilled.
- When the stop fills, record it: `ticket.py fill --index N --leg stop --qty Q --price P` (`--paper`
  in paper mode).
- At the target, or by `flat_by` (15:00 IST), get out:
  1. Ask Kite how many shares are held and the last price: `get_positions`, `get_ltp` (read only).
  2. `ticket.py flatten --symbol RELIANCE --qty 62 --price 1199.40` writes a SELL MIS LIMIT at the last
     price minus `stop_limit_buffer_pct`, rounded down to the tick, to `trader/orders.json`.
  3. `/india-risk`. A sell that only closes what the user holds is never blocked by the daily limits,
     the entry window or flat_by.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. **Cancel the open stop order first.** Live: `get_orders` shows the SL order (TRIGGER PENDING);
     `cancel_order` with variety regular and its order_id (Needs approval); check `get_orders` shows it
     CANCELLED. If it shows COMPLETE, the stop already sold you out: do not place the exit. Then
     `place_order` with the exit ticket, then `ticket.py result --index 1 --leg exit --status placed
     --order-id <id> --stop-cancelled`. `result` refuses an exit while the log shows the stop open and
     `--stop-cancelled` is missing. Paper: `fill --index 1 --paper --price P` cancels the paper stop.
  6. If the LIMIT does not fill (the price fell through it), make a new flatten ticket at the new price.
     Zerodha squares off MIS itself at 15:12 (F&O stocks) or 15:25, for ₹50 + 18% GST an order.

## Example: the reel

```
TICKET 1 of 1, PAPER mode (nothing is sent to Kite), Zerodha Kite
  entry: BUY 62 RELIANCE on NSE, intraday (MIS), limit 1194.80, good for today
  stop: SELL 62 RELIANCE on NSE, intraday (MIS), stop-loss: trigger 1183.60, limit 1177.60, good for today; placed only after the entry fills
  target 1203.60: watched by india-alerts, not placed (the exit is ticket.py flatten)
  max loss about ₹767.69 if the stop fills at 1183.60, charges included (₹1,139.58 at its limit 1177.60; a gap below that leaves the stop unfilled)
  why: opening-range breakout on RELIANCE: 62 shares, bound by your position cap (62 x 11.20 = ₹694.40 at the stop plus ₹73.29 charges = ₹767.69)
  READY: india-risk PASS for both orders, waiting for your yes
  Kite place_order input, entry (paper: shown, not sent):
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
     "quantity": 62, "product": "MIS", "order_type": "LIMIT", "price": 1194.8, "validity": "DAY", "tag": "india-orb"}
  Kite place_order input, stop, only after the entry fill is confirmed with get_orders / get_positions (paper: shown, not sent):
    {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "SELL",
     "quantity": 62, "product": "MIS", "order_type": "SL", "price": 1177.6, "trigger_price": 1183.6,
     "validity": "DAY", "tag": "india-stop"}
  Reply "yes" to take this trade (the entry and its stop), or "no" to skip it.
```

The user replies "yes". In paper mode Claude records it and the paper fill (`fill --index 1 --paper
--price 1194.20`: cash ₹25,959.60, 62 RELIANCE, the paper stop resting). In live mode Claude calls
`place_order` with the entry JSON, the card reads "Claude wants to use Place order" with two buttons,
Allow once and Deny, and the stop follows only after the fill is confirmed. The `role`, `group`,
`after`, `why` and the plan stay in the file; they are never sent.

## What it never does

- Never places an order without all three gates, and never two trades on one yes.
- Never calls an order tool in paper mode.
- Never places the stop before the entry fill is confirmed, and never a stop for more shares than held.
- Never places a second resting SELL for the target next to the stop.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys, secrets, access tokens or passwords.
- Never switches `"paper"` to false. The user does that themselves.
- Never sends a MARKET order (Kite needs market protection the MCP tool cannot send), and never F&O.
- Never promises a fill price: a LIMIT may not fill, and an SL can fill at its limit or not at all.

## Safety

- Not financial advice. This is software that reads public data and does arithmetic. A PASS is a rules
  check, not a recommendation.
- SEBI's study of FY23: about 7 in 10 (71%) individual intraday traders in the cash segment made a loss.
- No trade goes through until you say yes. Paper first (`"paper": true`); going live is the user's own
  decision, in their own config.
- Nothing in this pack places an order except india-broker, and only when all three hold: india-risk
  passed that exact order today, the user typed an explicit yes for that one trade (the entry and its
  stop) in the chat, and the user clicked Allow once on Claude's own card for each place_order call
  because the order tools are set to "Needs approval". Never set an order tool to "Always allow".
- No F&O (never on in this pack), no short selling, no margin unless the config turns it on and the
  user confirms.
- Kite API keys live in environment variables of the self-hosted Kite MCP server, never in the chat.
