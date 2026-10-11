from powerspike.ingest import kalshi


def _candle(ts: int, trade=None, bid=None, ask=None, vol="0.00") -> dict:
    d = {"end_period_ts": ts, "volume_fp": vol, "price": {}, "yes_bid": {}, "yes_ask": {}}
    if trade is not None:
        d["price"] = {"close_dollars": trade}
    if bid is not None:
        d["yes_bid"] = {"close_dollars": bid}
    if ask is not None:
        d["yes_ask"] = {"close_dollars": ask}
    return d


def test_trade_close_wins_over_midpoint():
    (row,) = kalshi.parse_candles([_candle(1_700_000_000, trade="0.2100", bid="0.1700", ask="0.2000", vol="69.15")])
    assert (row["price"], row["volume"]) == (0.21, 69.15)


def test_quote_only_candle_uses_midpoint():
    (row,) = kalshi.parse_candles([_candle(1_700_000_000, bid="0.1800", ask="0.2400")])
    assert (row["price"], row["volume"]) == (0.21, 0.0)


def test_empty_book_after_close_is_dropped():
    # After a market closes Kalshi reports bid 0.00 / ask 1.00; the midpoint 0.50 is not a price.
    assert kalshi.parse_candles([_candle(1_700_000_000, bid="0.0000", ask="1.0000")]) == []
    assert kalshi.parse_candles([_candle(1_700_000_000, bid="0.0000", ask="0.3000")]) == []  # one-sided book, same
