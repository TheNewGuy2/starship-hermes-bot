from starship_engine.condor import pick_condor_20delta_20wide


class DummyGreeks:
    def __init__(self, event_symbol, delta, volatility=0.20):
        self.event_symbol = event_symbol
        self.delta = delta
        self.volatility = volatility


def test_condor_picker_basic(monkeypatch):
    """
    Uses monkeypatch to stub parse_spxw_event_symbol so we don't depend on your real symbol parser format.
    """
    from starship_engine import condor as condor_mod

    class Parsed:
        def __init__(self, right, strike):
            self.right = right
            self.strike = strike

    def fake_parse(sym: str):
        # format: "C:6900" or "P:6700"
        right, strike = sym.split(":")
        return Parsed(right, float(strike))

    monkeypatch.setattr(condor_mod, "parse_spxw_event_symbol", fake_parse)

    greeks = {}
    # Put deltas around -0.20
    for strike, d in [(6700, -0.18), (6690, -0.20), (6680, -0.22), (6670, -0.25)]:
        sym = f"P:{strike}"
        greeks[sym] = DummyGreeks(sym, d)

    # Call deltas around +0.20
    for strike, d in [(6900, 0.18), (6910, 0.20), (6920, 0.22), (6930, 0.25)]:
        sym = f"C:{strike}"
        greeks[sym] = DummyGreeks(sym, d)

    # Add strikes for wings (±20)
    for strike, d in [(6670, -0.25), (6690, -0.20)]:
        pass
    # Already included enough strikes for nearest wing selection

    c = pick_condor_20delta_20wide(greeks, target_abs_delta=0.20, wing_width=20.0, delta_neutral=False)
    assert c is not None
    assert c.short_put.right == "P"
    assert c.short_call.right == "C"
    assert abs(c.width_put - 20.0) <= 1e-6
    assert abs(c.width_call - 20.0) <= 1e-6
