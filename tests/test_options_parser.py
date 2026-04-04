import pytest

from starship_engine.options import parse_spxw_event_symbol


@pytest.mark.parametrize(
    "sym, right, strike",
    [
        # OCC / tasty canonical format (strike * 1000, 8 digits)
        ("SPXW220520C04025000", "C", 4025.0),
        ("SPY221118C00400000", "C", 400.0),
        ("AAPL220617P00150000", "P", 150.0),
        ("AAPL220617C00000500", "C", 0.5),
        ("SPXW220520P04025000", "P", 4025.0),

        # With whitespace padding in root (docs show this style)
        ("SPXW 220520C04025000", "C", 4025.0),

        # With leading dot (dxFeed-style)
        (".SPXW220520C04025000", "C", 4025.0),

        # Legacy / short strike fallback (if your feed ever emits it)
        (".SPXW260105C2800", "C", 2800.0),
        (".SPXW260105P4850", "P", 4850.0),

        # dxFeed-ish strike*1000 without strict 8 digits (fallback tolerance)
        (".SPXW260109C06950000", "C", 6950.0),
        (".SPXW260109P06945000", "P", 6945.0),
    ],
)
def test_parse_spxw_event_symbol(sym, right, strike):
    parsed = parse_spxw_event_symbol(sym)
    assert parsed is not None, f"Failed to parse: {sym}"
    assert parsed.right == right
    assert parsed.strike == pytest.approx(strike, abs=1e-9)


@pytest.mark.parametrize(
    "sym",
    [
        "",                      # empty
        "SPXW220520X04025000",    # invalid right
        "SPXW220520C",            # missing strike
        "SPXW220520CABC",         # non-numeric strike
        "RANDOMTEXT",             # junk
    ],
)
def test_parse_spxw_event_symbol_invalid(sym):
    assert parse_spxw_event_symbol(sym) is None
