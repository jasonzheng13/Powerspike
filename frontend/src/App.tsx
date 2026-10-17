import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchCompanies, fetchOutlook, fetchSpikes, fetchStock } from "./api";
import ClimatePanel from "./components/ClimatePanel";
import OutlookCard from "./components/OutlookCard";
import SearchBar from "./components/SearchBar";
import SpikeCard from "./components/SpikeCard";
import StockChart from "./components/StockChart";
import { fmtClock, spikeKey } from "./lib/format";
import { applyTheme, initialTheme, type Theme } from "./lib/theme";
import { parseQuery, syncUrl } from "./lib/url";
import type { Company, Days, Outlook, SpikesResponse, StockSeries } from "./types";

const EXAMPLES = ["NVDA", "TSLA", "AAPL", "MSFT"];

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [companies, setCompanies] = useState<Company[]>([]);
  const initial = useMemo(() => parseQuery(window.location.search), []);
  const [ticker, setTicker] = useState(initial.ticker);
  const [days, setDays] = useState<Days>(initial.days);

  const [stock, setStock] = useState<StockSeries | null>(null);
  const [spikesResp, setSpikesResp] = useState<SpikesResponse | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [explained, setExplained] = useState<Set<string>>(() => new Set());
  const [outlook, setOutlook] = useState<Outlook | null>(null);
  const [outlookError, setOutlookError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => {
    fetchCompanies().then(setCompanies).catch(() => setCompanies([]));
  }, []);

  // Bars and spikes on every search; everything downstream resets.
  useEffect(() => {
    syncUrl(ticker, days);
    setStock(null);
    setSpikesResp(null);
    setSelectedKey(null);
    setExplained(new Set());
    setOutlook(null);
    setOutlookError(null);
    setError(null);
    if (!ticker) return;
    const ctl = new AbortController();
    const quiet = (e: Error) => e.name !== "AbortError" && setError(e.message);
    fetchStock(ticker, days, ctl.signal).then(setStock).catch(quiet);
    fetchSpikes(ticker, days, ctl.signal).then(setSpikesResp).catch(quiet);
    return () => ctl.abort();
  }, [ticker, days]);

  // The outlook needs every spike's explanation; wait until the cards have fetched them so the
  // slow call hits the server's memo instead of queueing behind the cards at the news index.
  const spikes = useMemo(() => spikesResp?.spikes ?? [], [spikesResp]);
  const allExplained = spikesResp !== null && spikes.every((s) => explained.has(spikeKey(s)));
  useEffect(() => {
    if (!ticker || !allExplained || spikesResp?.ticker !== ticker) return;
    const ctl = new AbortController();
    fetchOutlook(ticker, days, ctl.signal)
      .then(setOutlook)
      .catch((e: Error) => e.name !== "AbortError" && setOutlookError(e.message));
    return () => ctl.abort();
  }, [ticker, days, allExplained, spikesResp]);

  const onExplained = useCallback((key: string) => setExplained((cur) => (cur.has(key) ? cur : new Set(cur).add(key))), []);
  const newestFirst = useMemo(() => [...spikes].reverse(), [spikes]);
  const pending = spikes.filter((s) => !explained.has(spikeKey(s))).length;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Powerspike</h1>
          <p>Every spike in a tech stock, the news behind it, and whether the betting market moved first.</p>
        </div>
        <SearchBar ticker={ticker} days={days} companies={companies} onSearch={setTicker} onDays={setDays} />
        <button className="btn ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Switch theme">
          {theme === "dark" ? "Light" : "Dark"}
        </button>
      </header>

      {error && <div className="panel status error">{error}</div>}

      {!ticker && !error && (
        <section className="panel empty">
          <h2>Pick a stock to start</h2>
          <p>
            Powerspike pulls the last {days} days of one-minute prices, marks the moves that were big, backed by volume and
            held, reads the news around each one, and checks whether the Polymarket contract on the company repriced before
            the stock did. Then it says whether the current AI news climate reads bullish or bearish for that stock.
          </p>
          <div className="chips">
            {EXAMPLES.map((t) => (
              <button type="button" className="chip pick" key={t} onClick={() => setTicker(t)}>
                {t}
              </button>
            ))}
          </div>
        </section>
      )}

      {ticker && !stock && !error && <div className="panel status">Loading {ticker}: first visits pull a month of bars…</div>}

      {ticker && stock && (
        <>
          <p className="caption">
            {stock.name}
            {stock.sector ? `, ${stock.sector}` : ""}. Bars to {fmtClock(stock.as_of)} UTC.
            {spikesResp ? ` ${spikes.length} spike${spikes.length === 1 ? "" : "s"} in ${days} days.` : " Running the detector…"}
            {!stock.contract && " No liquid Polymarket contract on this company, so timing against the odds is off."}
          </p>
          <div className="grid">
            <StockChart
              stock={stock}
              spikes={spikes}
              selectedKey={selectedKey}
              theme={theme}
              onSpikeClick={(s) => setSelectedKey(spikeKey(s))}
            />
            <OutlookCard outlook={outlook} loading={allExplained} error={outlookError} pending={pending} />
          </div>
          <div className="grid">
            <section className="spikes" aria-label="Spikes">
              <h2>Spikes, newest first</h2>
              {spikesResp && spikes.length === 0 && (
                <p className="muted">
                  Nothing in the last {days} days was big, volume-backed and sticky enough to count. Try 30 days or another ticker.
                </p>
              )}
              {newestFirst.map((s) => (
                <SpikeCard
                  key={spikeKey(s)}
                  ticker={ticker}
                  days={days}
                  spike={s}
                  selected={selectedKey === spikeKey(s)}
                  onSelect={() => setSelectedKey((cur) => (cur === spikeKey(s) ? null : spikeKey(s)))}
                  onExplained={onExplained}
                />
              ))}
            </section>
            <ClimatePanel />
          </div>
        </>
      )}
    </div>
  );
}
