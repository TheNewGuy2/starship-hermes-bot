from starship_engine.apps.heston.logic import HestonGateParams, evaluate_heston_gate


def test_gate_disabled_passes():
    params = HestonGateParams(enabled=False)
    res = evaluate_heston_gate(
        enabled=params.enabled,
        iv_atm=None,
        rv30=0.2,
        rv60=0.2,
        rv90=0.2,
        vov=0.0,
        params=params,
    )
    assert res.ok is True


def test_gate_blocks_low_vrp():
    params = HestonGateParams(enabled=True, vrp_min=1.25, mr_max=0.0, vov_max=None)
    res = evaluate_heston_gate(
        enabled=params.enabled,
        iv_atm=0.10,
        rv30=0.10,
        rv60=0.10,
        rv90=0.10,
        vov=0.0,
        params=params,
    )
    # vrp=1.0 < 1.25
    assert res.ok is False


def test_gate_blocks_low_rv60_floor():
    params = HestonGateParams(enabled=True)
    res = evaluate_heston_gate(
        enabled=params.enabled,
        iv_atm=0.30,
        rv30=0.20,
        rv60=0.01,  # below default rv60_min=0.05
        rv90=0.20,
        vov=0.0,
        params=params,
    )
    assert res.ok is False
    assert "RV60 unavailable/low" in res.reason


def test_gate_requires_mr_available():
    params = HestonGateParams(enabled=True)
    res = evaluate_heston_gate(
        enabled=params.enabled,
        iv_atm=0.30,
        rv30=float("nan"),  # force MR unavailable
        rv60=0.10,
        rv90=0.20,
        vov=0.0,
        params=params,
    )
    assert res.ok is False
    assert res.reason == "MR unavailable"


def test_gate_blocks_high_vrp_when_capped():
    params = HestonGateParams(
        enabled=True, vrp_min=1.25, vrp_max=2.0, mr_max=0.05, rv60_min=0.05
    )
    res = evaluate_heston_gate(
        enabled=params.enabled,
        iv_atm=0.40,  # vrp = 0.40 / 0.10 = 4.0 > vrp_max
        rv30=0.10,
        rv60=0.10,
        rv90=0.10,
        vov=0.0,
        params=params,
    )
    assert res.ok is False
    assert "VRP too high" in res.reason
