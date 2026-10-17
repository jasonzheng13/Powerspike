"""Direction priors: every ETF in exposure.yaml has an expected sign, every market an event_sign."""

from powerspike import config, expose


def test_every_etf_has_an_expected_sign():
    for et, cfg in config.exposure().items():
        for t in cfg["etfs"]:
            assert cfg["expected"][t] in (-1, 0, 1), f"{et}:{t}"


def test_expected_signs_match_direction_notes():
    fed = config.exposure()["fed_rates"]["expected"]
    assert fed["TLT"] == 1 and fed["KRE"] == -1          # cut odds up: duration up, bank NIM down


def test_markets_declare_event_sign():
    assert config.market("poly-fed-oct-hike")["event_sign"] == -1   # YES = hike, so YES up = cut odds down
    assert config.market("kalshi-unemp-aug")["event_sign"] == 1      # YES = unemployment up = weaker labour
    assert expose.event_sign("poly-fed-oct-hike") == -1
    assert expose.event_sign("synthetic-demo") == 1              # unknown market defaults to +1


def test_expected_sign_lookup_defaults_to_zero():
    assert expose.expected("fed_rates")["TLT"] == 1
    assert expose.expected("fed_rates").get("ZZZ", 0) == 0
    assert expose.expected("nope") == {}


def test_all_etfs_is_the_deduplicated_union_in_config_order():
    allt = expose.all_etfs()
    assert allt[:6] == ["TLT", "IEF", "HYG", "LQD", "KRE", "XLF"]
    assert len(allt) == len(set(allt)) and "SMH" in allt and "JNK" in allt


# ---- companies.yaml (stock pivot) ------------------------------------------------------

def test_companies_are_configured_with_well_formed_contracts():
    cos = config.companies()
    tickers = [c["ticker"] for c in cos]
    assert "NVDA" in tickers and len(tickers) == len(set(tickers))
    ids = []
    for c in cos:
        assert c["name"] and c["sector"] and c["news_query"], c["ticker"]
        assert c["ticker"] == c["ticker"].upper()
        for k in c["contracts"]:
            assert k["venue"] == "polymarket" and k["id"].startswith("poly:"), k
            assert k["event_sign"] in (-1, 1) and k["name"] and k["question"]
            assert str(k["token_id"]).isdigit(), k["id"]  # a CLOB token id, not a slug
            ids.append(k["id"])
    assert len(ids) == len(set(ids))
    assert config.company("NVDA")["contracts"], "NVDA needs a contract for the demo"


def test_company_lookup_is_case_insensitive_and_none_when_unknown():
    assert config.company("nvda")["ticker"] == "NVDA"
    assert config.company("ZZZQ") is None
