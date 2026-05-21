import numpy as np
import pytest
from data import load_data, ZoneData, MergeChain
from simulation import simulate_harvest, check_puzzle_completion

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


# --- check_puzzle_completion tests ---


def _make_chain(items):
    return MergeChain(name="test", items=items)


def test_sufficient_inventory_passes():
    chain = _make_chain(["item_1", "item_2", "item_3"])
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=10,
                    composition={"item_1": 20.0})  # 2 items
    assert check_puzzle_completion({"item_1": 4}, zone, 1.0, [chain]) is True


def test_insufficient_inventory_fails():
    chain = _make_chain(["item_1", "item_2", "item_3"])
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=10,
                    composition={"item_1": 20.0})  # 2 items, need 4 total
    assert check_puzzle_completion({"item_1": 3}, zone, 1.0, [chain]) is False


def test_lower_level_items_merge_up():
    chain = _make_chain(["item_1", "item_2", "item_3"])
    # Need 2x item_2. Cost = 2x3^1 = 6 base units. Have 6x item_1 = 6. Pass.
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=10,
                    composition={"item_2": 20.0})  # 2 tiles
    assert check_puzzle_completion({"item_1": 6}, zone, 1.0, [chain]) is True


def test_higher_level_cannot_downgrade():
    chain = _make_chain(["item_1", "item_2", "item_3"])
    # Need 2x item_1. Have only item_3 (cannot downgrade). Fail.
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=10,
                    composition={"item_1": 20.0})  # 2 tiles
    assert check_puzzle_completion({"item_3": 10}, zone, 1.0, [chain]) is False


def test_partial_completion():
    chain = _make_chain(["item_1", "item_2"])
    # 4 tiles of item_1; 50% = 2 merges = 4 item_1 needed.
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=20,
                    composition={"item_1": 20.0})  # 4 tiles
    assert check_puzzle_completion({"item_1": 4}, zone, 0.5, [chain]) is True
    assert check_puzzle_completion({"item_1": 4}, zone, 1.0, [chain]) is False
