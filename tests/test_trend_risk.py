from starship_engine.apps.trend_risk.logic import (
    adx_level_points,
    adx_slope_points,
    clamp_score,
    di_spread_points,
    rv_modifier,
    stretch_points,
)


def test_adx_level_points_buckets():
    assert adx_level_points(10.0, 20, 25, 30, 35) == 0
    assert adx_level_points(20.0, 20, 25, 30, 35) == 1
    assert adx_level_points(24.9, 20, 25, 30, 35) == 1
    assert adx_level_points(25.0, 20, 25, 30, 35) == 2
    assert adx_level_points(29.9, 20, 25, 30, 35) == 2
    assert adx_level_points(30.0, 20, 25, 30, 35) == 3
    assert adx_level_points(34.9, 20, 25, 30, 35) == 3
    assert adx_level_points(35.0, 20, 25, 30, 35) == 4


def test_adx_slope_points_buckets():
    assert adx_slope_points(0.4, 0.5, 1.5) == 0
    assert adx_slope_points(0.5, 0.5, 1.5) == 1
    assert adx_slope_points(1.49, 0.5, 1.5) == 1
    assert adx_slope_points(1.5, 0.5, 1.5) == 2


def test_di_spread_points_buckets():
    assert di_spread_points(5.9, 6, 12) == 0
    assert di_spread_points(6.0, 6, 12) == 1
    assert di_spread_points(11.9, 6, 12) == 1
    assert di_spread_points(12.0, 6, 12) == 2


def test_stretch_points_buckets():
    assert stretch_points(0.6, 0.7, 1.3) == 0
    assert stretch_points(0.7, 0.7, 1.3) == 1
    assert stretch_points(1.29, 0.7, 1.3) == 1
    assert stretch_points(1.3, 0.7, 1.3) == 2


def test_rv_modifier():
    assert rv_modifier(0.18, 0.20) == -1
    assert rv_modifier(0.20, 0.20) == 0
    assert rv_modifier(0.22, 0.20) == 1


def test_clamp_score():
    assert clamp_score(-2) == 0
    assert clamp_score(0) == 0
    assert clamp_score(4.2) == 4
    assert clamp_score(10.0) == 10
    assert clamp_score(12.0) == 10
