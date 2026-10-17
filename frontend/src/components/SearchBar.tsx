import { useEffect, useState, type FormEvent } from "react";

import type { Company, Days } from "../types";

interface Props {
  ticker: string;
  days: Days;
  companies: Company[];
  onSearch: (ticker: string) => void;
  onDays: (days: Days) => void;
}

/** Ticker search with the configured companies as suggestions, and the 7-day / 30-day switch. */
export default function SearchBar({ ticker, days, companies, onSearch, onDays }: Props) {
  const [draft, setDraft] = useState(ticker);
  useEffect(() => setDraft(ticker), [ticker]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const t = draft.trim().toUpperCase();
    if (t) onSearch(t);
  };

  return (
    <form className="search" onSubmit={submit} role="search">
      <label className="search-field">
        <span className="visually-hidden">Ticker</span>
        <input
          list="companies"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Search a tech ticker, e.g. NVDA"
          autoComplete="off"
          spellCheck={false}
        />
        <datalist id="companies">
          {companies.map((c) => (
            <option value={c.ticker} key={c.ticker}>
              {c.name}
            </option>
          ))}
        </datalist>
      </label>
      <button type="submit" className="btn primary">
        Look up
      </button>
      <div className="days" role="group" aria-label="Window">
        {([7, 30] as Days[]).map((d) => (
          <button
            type="button"
            key={d}
            className={`pill${days === d ? " on" : ""}`}
            onClick={() => onDays(d)}
            aria-pressed={days === d}
          >
            {d} days
          </button>
        ))}
      </div>
    </form>
  );
}
