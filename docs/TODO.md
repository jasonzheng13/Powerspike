# To do

One at a time, top to bottom. Tick a box when its tests pass.

## 1. News reliability

GDELT refuses most requests from this machine, so a cold ticker is slow and spikes often have no
headlines.

- [ ] Measure GDELT's real penalty window (space requests 60 s apart, log status codes).
- [ ] Add a second news source per ticker (Yahoo Finance news is the obvious one) in `explain.py`,
      used when GDELT returns nothing.
- [ ] Re-check that `warm_stocks.py` fills every spike's explanation on NVDA.

## 2. Roll the contracts

Settled contracts are skipped automatically; the ticker then reads "no contract".

- [ ] MSFT: settles 2026-10-19. Find a new leg: `python scripts/find_markets.py --event-slug <slug>`,
      add it first in `backend/config/companies.yaml` with `closes:`.
- [ ] TSLA: settles 2026-10-30. Same.
- [ ] Run `pytest tests/test_config_signs.py`.

## 3. Review minors

- [ ] `explain._json_reply`: greedy `\{.*\}` regex; use `json.JSONDecoder().raw_decode` from the first `{`.
- [ ] `detect._onset`: reads the raw price, not the smoothed level; a stray print can set `start`.
- [ ] `api._contract_token`: swallows exceptions silently; log at warning.
- [ ] `api._token`: the 503 echoes the exception text to the client.
- [ ] `SpikeCard.tsx`: detects overnight spikes from the headline text; send a flag from the API.
- [ ] `stocks.stale`: a delisted ticker with cached bars re-hits yfinance every request.
- [ ] `explain._reserve_slot`: concurrent GDELT waiters can each eat one 429.
- [ ] Test `leadlag.react_contract` with the window past the end of the frame.

## 4. Portfolio polish

- [ ] Light theme: screenshot and check the chart colours and the outlook card.
- [ ] Add a screenshot to `README.md`.
- [ ] Record a short demo on a warmed server.

## 5. Optional

- [ ] "If you'd followed the signal": for each spike, what buying at confirmation returned over 1
      and 5 days, shown on the outlook card.
