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
    assert config.market("fed-oct-hold")["event_sign"] == -1   # YES = hold, so YES up = cut odds down
    assert config.market("recession")["event_sign"] == 1
    assert expose.event_sign("fed-oct-hold") == -1
    assert expose.event_sign("synthetic-demo") == 1              # unknown market defaults to +1


def test_expected_sign_lookup_defaults_to_zero():
    assert expose.expected("fed_rates")["TLT"] == 1
    assert expose.expected("fed_rates").get("ZZZ", 0) == 0
    assert expose.expected("nope") == {}


def test_all_etfs_is_the_deduplicated_union_in_config_order():
    allt = expose.all_etfs()
    assert allt[:6] == ["TLT", "IEF", "HYG", "LQD", "KRE", "XLF"]
    assert len(allt) == len(set(allt)) and "SMH" in allt and "JNK" in allt
