import numpy as np
import pytest
from data import load_data
from simulation import simulate_harvest

EXCEL = "data/Discovery Event Layout Generator.xlsx"


@pytest.fixture(scope="module")
def game_data():
    return load_data(EXCEL)


@pytest.fixture
def rng():
    return np.random.default_rng(seed=42)


def test_zero_grinding_yields_empty(game_data, rng):
    zone3 = game_data.zones[3]
    inv = simulate_harvest(zone3, 0.0, 3, 5, game_data, rng)
    assert sum(inv.values()) == 0


def test_full_grinding_yields_items(game_data, rng):
    zone3 = game_data.zones[3]
    inv = simulate_harvest(zone3, 1.0, 3, 5, game_data, rng)
    assert sum(inv.values()) > 50


def test_harvest_gives_currency_from_harvest_away(game_data, rng):
    zone3 = game_data.zones[3]
    inv = simulate_harvest(zone3, 1.0, 3, 5, game_data, rng)
    assert inv.get("Event_LakeCottage_Currency_1", 0) > 0


def test_partial_grinding_scales_proportionally(game_data):
    zone3 = game_data.zones[3]
    inv_50 = simulate_harvest(zone3, 0.5, 3, 5, game_data, np.random.default_rng(99))
    inv_100 = simulate_harvest(zone3, 1.0, 3, 5, game_data, np.random.default_rng(99))
    assert sum(inv_50.values()) < sum(inv_100.values())
