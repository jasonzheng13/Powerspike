# Powerspike, in one page

Type a tech ticker. See every spike in its price, the news behind each one, whether the Polymarket
contract on the company moved first, and a bullish / bearish read for right now.

## Run it

```bash
cd backend && source ../.venv/bin/activate
uvicorn powerspike.api:app --port 8010
python scripts/warm_stocks.py NVDA TSLA AAPL MSFT --api http://localhost:8010   # slow news index: warm before a demo
```

Open http://localhost:8010/?t=NVDA

## The pieces

```mermaid
flowchart LR
  subgraph Sources
    YF[yfinance<br/>1-min bars]
    PM[Polymarket<br/>contract odds]
    GD[GDELT<br/>headlines]
    GM[Gemini<br/>text only]
  end
  subgraph Backend
    ST[stocks.py<br/>pull + cache]
    DE[detect.py<br/>find spikes]
    LL[leadlag.py<br/>odds vs stock timing]
    EX[explain.py<br/>why + theme, AI climate]
    OU[outlook.py<br/>bullish / bearish]
    API[api.py]
  end
  subgraph Frontend
    UI[Search · Chart · Spike cards<br/>Outlook · AI news]
  end
  YF --> ST --> DE --> LL
  PM --> ST
  DE --> EX
  GD --> EX
  GM --> EX
  DE & EX & LL --> OU
  GM --> OU
  DE & LL & EX & OU --> API --> UI
```

## One search

```mermaid
sequenceDiagram
  participant B as Browser
  participant A as API
  B->>A: bars + spikes
  A-->>B: chart, spike cards, "contract moved 29 min before"
  loop each spike
    B->>A: explain (one news request per 15 s)
    A-->>B: why, theme, AI badge
  end
  B->>A: climate (last 72 h of AI news)
  B->>A: outlook (after every card is explained)
  A-->>B: bullish / bearish / neutral + drivers
```

## The verdict

```mermaid
flowchart TB
  S[past spikes, tagged by theme] --> SE[how much this stock<br/>moves per theme]
  C[AI news climate<br/>theme, direction, weight] --> N[news term]
  SE --> N
  O[contract odds, 7-day trend] --> T[contract term]
  N & T --> V{sum vs ±0.5%}
  V --> BU[bullish] & BE[bearish] & NE[neutral]
```

Confidence = size of the sum × how many spikes it rests on. The model writes the sentences; it
never picks the label.

## Spike rules

- Big: 15-minute move vs the last 4 hours, robust z ≥ 4. Overnight gaps are judged on their own scale.
- Real: volume 2× usual.
- Sticks: still there 30 minutes later.
- Calibrated on 30 days: NVDA 5, TSLA 12, AAPL 3, MSFT 7 spikes.

## Know before showing

- GDELT refuses most requests from this machine; a cold ticker is slow. Warm first.
- Contracts settle: MSFT's on Oct 19, TSLA's on Oct 30. Add a new leg in
  `backend/config/companies.yaml` (`python scripts/find_markets.py --event-slug <slug>`).
- Amazon and Meta have no usable contract; they still get spikes, news and an outlook.
- Not investment advice. Every card says so.

## Files

| What | Where |
|---|---|
| Companies and contracts | `backend/config/companies.yaml` |
| Detector | `backend/powerspike/detect.py` |
| Odds vs stock timing | `backend/powerspike/leadlag.py` |
| News and climate | `backend/powerspike/explain.py` |
| Verdict | `backend/powerspike/outlook.py` |
| API | `backend/powerspike/api.py` |
| Screen | `frontend/src/App.tsx`, `frontend/src/components/` |
| Decisions | `docs/superpowers/specs/2026-10-16-stock-pivot-design.md` |
