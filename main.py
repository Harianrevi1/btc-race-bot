#!/usr/bin/env python3
"""Dip-buy mean-reversion bot for the BTC 24h race (agent-fun/bars-replay/v1).

Strategy, in plain words: Coinbase Wrapped BTC usually wiggles in a range
over a day, so this bot buys a dip and sells the bounce instead of chasing
a trend. It watches the 1-minute closes, keeps a slow EMA(96) as the
"fair" line, and only acts on a clear dip below it.

  - Entry: flat, and the latest close is at least 0.60% below EMA(96),
    and at least 45 bars have passed since the last sell filled.
    Buys 50% of equity (never more than the cash on hand).
  - Exit: the whole position, when the close is 1.00% above the fill
    price (take profit) or 0.80% below it (stop loss).
  - One position at a time, long only, no leverage, no shorts.

Costs matter more than cleverness here: a full round trip pays about
26 bps in fees, slippage and price impact, so the bot trades rarely
(5 trades on the 1,440-bar dev window) and only for moves bigger
than that cost. On the dev window it nets about +$7.57 on $1,000
with a 0.44% maximum drawdown, versus about +$1.73 for buy-and-hold.

Protocol: newline-delimited JSON on stdin/stdout. The harness sends
one `hello`, then one `batch` per minute, then `done`. The bot answers
every batch with exactly one `reply` echoing its seq. Everything is
deterministic: no randomness, no clock, no network, no files outside
the repo. All money math is integer (micro-USDC quote units); the EMA
runs in float64, which is deterministic for the same inputs.
"""

import json
import sys

# --- strategy constants (tuned once on the published dev window) ---
EMA_N = 96            # slow EMA length, in 1-minute bars
DIP_BPS = 60          # buy only this far below the EMA (0.60%)
TAKE_PROFIT_BPS = 100  # sell when up 1.00% from the fill price
STOP_LOSS_BPS = 80    # sell when down 0.80% from the fill price
SIZE_BPS = 5000       # buy size: 50% of equity
COOLDOWN_BARS = 45    # bars to wait after a sell fills before buying again
MAX_SLIPPAGE_BPS = 100  # 1.00%: fills past this are refused, so stay wide
MIN_NOTIONAL = 1_000_000  # 1 USDC, in raw quote units (fees.min_notional)


def main():
    hello = None
    symbol = None
    price_scale = 5
    qty_scale = 8
    quote_scale = 6
    warmup_bars = 0

    closes = []          # closes seen so far, as floats (raw price units)
    ema = None           # current EMA(96) value, float
    k = 2.0 / (EMA_N + 1)

    entry_price = None   # fill price of the open position (raw price units)
    last_sell_bar = -10 ** 9  # bar index of the last sell fill
    bar_index = 0        # counts bars seen (gaps add nothing)
    order_no = 0         # unique client-id counter

    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mtype = msg.get("type")

        if mtype == "hello":
            hello = msg
            syms = msg.get("symbols") or []
            if syms:
                symbol = syms[0].get("symbol")
                price_scale = int(syms[0].get("price_scale", price_scale))
                qty_scale = int(syms[0].get("qty_scale", qty_scale))
            cap = msg.get("capital") or {}
            quote_scale = int(cap.get("quote_scale", quote_scale))
            warmup_bars = int(msg.get("warmup_bars") or 0)
            continue

        if mtype == "done":
            break

        if mtype != "batch":
            continue

        seq = msg.get("seq", 0)
        bars = msg.get("bars") or []
        fills = msg.get("fills") or []
        account = msg.get("account") or {}

        # Learn from fills first: a buy fill sets the entry price, a sell
        # fill starts the cooldown. The account below is authoritative
        # for whether a position is open.
        for fill in fills:
            side = fill.get("side")
            if side == "buy" and fill.get("qty"):
                entry_price = float(fill.get("price", 0))
            elif side == "sell":
                last_sell_bar = bar_index
                entry_price = None

        # Append this batch's closes and update the EMA. A minute with
        # no bar adds nothing (a gap is not a flat bar).
        for bar in bars:
            if bar.get("symbol") != symbol:
                continue
            c = float(bar.get("c", 0))
            closes.append(c)
            if ema is None:
                ema = c
            else:
                ema = c * k + ema * (1.0 - k)
            bar_index += 1

        # Position and cash, straight from the harness account.
        positions = account.get("positions") or []
        position_qty = 0
        position_cost = 0
        for pos in positions:
            if pos.get("symbol") == symbol:
                position_qty = int(pos.get("qty", 0))
                position_cost = int(pos.get("cost_quote_units", 0))
        cash = int(account.get("cash_quote_units", 0))
        equity = int(account.get("equity_quote_units", cash))

        # If a position is open but its fill price was never seen (for
        # example after a replay restart), fall back to the position's
        # cost: entry price = cost * 10^(qty+price scale) / (qty * 10^quote).
        if position_qty > 0 and not entry_price and position_cost > 0:
            entry_price = (
                position_cost * (10 ** (qty_scale + price_scale))
                / (position_qty * (10 ** quote_scale))
            )

        orders = []
        have_history = len(closes) >= EMA_N and bar_index > warmup_bars
        if have_history and bars:
            last_close = closes[-1]
            if position_qty == 0:
                # Flat: buy the dip below the slow EMA.
                dip_line = ema * (1.0 - DIP_BPS / 10000.0)
                cooled_down = (bar_index - last_sell_bar) >= COOLDOWN_BARS
                if last_close < dip_line and cooled_down:
                    notional = equity * SIZE_BPS // 10000
                    if notional > cash:
                        notional = cash
                    if notional >= MIN_NOTIONAL:
                        order_no += 1
                        orders.append({
                            "side": "buy",
                            "client_id": "buy-%d" % order_no,
                            "symbol": symbol,
                            "notional_quote_units": str(notional),
                            "max_slippage_bps": MAX_SLIPPAGE_BPS,
                        })
            elif entry_price:
                # Long: take profit or stop loss on the fill price.
                ret_bps = (last_close - entry_price) / entry_price * 10000.0
                if ret_bps >= TAKE_PROFIT_BPS or ret_bps <= -STOP_LOSS_BPS:
                    order_no += 1
                    orders.append({
                        "side": "sell",
                        "client_id": "sell-%d" % order_no,
                        "symbol": symbol,
                        "qty": str(position_qty),
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    })

        out.write(json.dumps(
            {"type": "reply", "seq": seq, "orders": orders},
            separators=(",", ":"),
        ) + "\n")
        out.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
