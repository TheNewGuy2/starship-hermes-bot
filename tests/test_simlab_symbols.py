from starship_simlab.ic_filter import _map_symbol_for_source


def test_map_symbol_stooq_spx() -> None:
    assert _map_symbol_for_source("^GSPC", "stooq") == "^spx"
    assert _map_symbol_for_source("gspc", "stooq") == "^spx"


def test_map_symbol_stooq_vix() -> None:
    assert _map_symbol_for_source("^VIX", "stooq") == "vi.f"
    assert _map_symbol_for_source("vix", "stooq") == "vi.f"
