from starship_engine.domain.handlers import StreamHealthHandler


def test_stream_health_idle_callback():
    calls: list[tuple[float, bool]] = []
    plugin = StreamHealthHandler(
        idle_warn_seconds=10,
        idle_slack_cooldown=30,
        on_idle=lambda idle, warned_no_candle: calls.append((idle, warned_no_candle)),
    )

    res1 = plugin.check_idle(now=100.0)
    assert res1.warned is False

    plugin.last_any_stream_event_ts = 50.0
    res2 = plugin.check_idle(now=70.0)
    assert res2.warned is True
    assert calls

    res3 = plugin.check_idle(now=80.0)
    assert res3.warned is True
    assert len(calls) == 2

    res4 = plugin.check_idle(now=120.0)
    assert res4.warned is True
    assert len(calls) == 3
