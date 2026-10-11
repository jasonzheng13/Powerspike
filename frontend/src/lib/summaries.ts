import { useEffect, useState } from "react";

import { fetchAlerts, fetchSeries } from "../api";
import type { Alert, DetectorInputs, Market, Series } from "../types";

export interface Summary {
  series: Series;
  alerts: Alert[];
}

/** Series + alerts for every market, shared by the overview strip and the sidebar. */
export function useMarketSummaries(markets: Market[], params: DetectorInputs): Record<string, Summary> {
  const [data, setData] = useState<Record<string, Summary>>({});

  useEffect(() => {
    const ctl = new AbortController();
    const t = setTimeout(() => {
      markets.forEach((m) => {
        Promise.all([fetchSeries(m.id, ctl.signal), fetchAlerts(m.id, params, ctl.signal)])
          .then(([series, resp]) => setData((d) => ({ ...d, [m.id]: { series, alerts: resp.alerts } })))
          .catch(() => {
            /* a market without data just stays in its loading state */
          });
      });
    }, 250);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [markets, params]);

  return data;
}
