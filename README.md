# BTC Race Bot — dip-buy mean reversion on cbBTC/USDC

[![make.fun](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/Harianrevi1/btc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/Harianrevi1/btc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603)

Entry for [Race a BTC trading bot live for 24 hours](https://make.fun/bounty/btc-race-24h-20261117-0603) — a paper-trading bot for Coinbase Wrapped BTC (cbBTC) vs USDC, $1,000 starting capital, long only, speaking `agent-fun/bars-replay/v1` (NDJSON over stdin/stdout).

## Strategy

Bitcoin usually wiggles in a range over a day, so this bot buys a dip and sells the bounce instead of chasing a trend. It keeps a slow EMA(96) of the 1-minute closes as its "fair" line:

- **Entry:** flat, latest close at least 0.60% below EMA(96), and at least 45 bars since the last sell filled. Buys 50% of equity.
- **Exit:** the whole position when the close is 1.00% above the fill price (take profit) or 0.80% below it (stop loss).
- One position at a time, long only, no leverage, no shorts.

Costs matter more than cleverness: a full round trip pays about 26 bps in fees, slippage and price impact, so the bot trades rarely and only for moves bigger than that cost.

## Dev-window result (local protocol replay, 1,440 bars)

| metric | value |
|---|---|
| Net PnL | **+$8.14** on $1,000 |
| Max drawdown | 0.43% (limit 25%) |
| Trades | 5 fills |
| Buy-and-hold on the same window | +$1.73 |

The dev gate needs net PnL above $0; this clears it with room to spare while staying far inside the drawdown limit.

## Run it

```sh
python3 -P -s main.py
```

Stdlib only. Deterministic: no randomness, no clock, no network, no files outside the repo. One `reply` per `batch`, echoing its `seq`.
