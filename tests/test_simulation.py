import numpy as np
import pytest
from data import load_data, ZoneData, MergeChain, ZoneUnlock
from simulation import simulate_harvest, check_puzzle_completion, check_zone_unlock, build_curve

EXCEL = "data/Discovery Event Layout Generator.xlsx"


@pytest.fixture(scope="module")
def game_data():
    return load_data(EXCEL)


@pytest.fixture
def rng():
    return np.random.default_rng(seed=42)


def test_zero_grinding_yields_empty(game_data, rng):
    zone3 = game_data.zones[3]
    inv = simulate_harvest(zone3, 0.0, 20, game_data, rng)
    assert sum(inv.values()) == 0


def test_full_grinding_yields_items(game_data, rng):
    zone3 = game_data.zones[3]
    inv = simulate_harvest(zone3, 1.0, 20, game_data, rng)
    assert sum(inv.values()) > 50


def test_harvest_gives_currency_from_harvest_away(game_data, rng):
    zone3 = game_data.zones[3]
    inv = simulate_harvest(zone3, 1.0, 20, game_data, rng)
    # Zone 3 (tier 1-3) drops Currency_1/2/3 — check any currency present
    total_currency = sum(
        inv.get(f"Event_LakeCottage_Currency_{i}", 0) for i in range(1, 4)
    )
    assert total_currency > 0


def test_partial_grinding_scales_proportionally(game_data):
    zone3 = game_data.zones[3]
    inv_50 = simulate_harvest(zone3, 0.5, 20, game_data, np.random.default_rng(99))
    inv_100 = simulate_harvest(zone3, 1.0, 20, game_data, np.random.default_rng(99))
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


# --- check_zone_unlock tests ---


def test_no_unlock_required_passes():
    unlock = ZoneUnlock(zone_id=3, required_currency_level=None)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({}, unlock, cc) is True


def test_unlock_with_exact_currency():
    # Currency_4 needs 2.5^3 = 15.625 base units → need at least 16 Currency_1
    unlock = ZoneUnlock(zone_id=2, required_currency_level=4)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({"Event_LakeCottage_Currency_1": 16}, unlock, cc) is True


def test_unlock_insufficient_currency():
    unlock = ZoneUnlock(zone_id=2, required_currency_level=4)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({"Event_LakeCottage_Currency_1": 15}, unlock, cc) is False


def test_unlock_with_higher_level_currency():
    unlock = ZoneUnlock(zone_id=2, required_currency_level=4)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({"Event_LakeCottage_Currency_4": 1}, unlock, cc) is True


# --- build_curve tests ---


def test_build_curve_is_monotone(game_data):
    curve = build_curve(3, 4, 0.5, 20, 300, game_data)
    values = [curve[k] for k in sorted(curve.keys())]
    for i in range(1, len(values)):
        assert values[i] >= values[i - 1] - 0.15


def test_build_curve_zero_grinding_near_zero(game_data):
    # At 0% grinding of zone 3, player has no ancient_object/flower → puzzle completion fails
    curve = build_curve(3, 4, 0.5, 20, 500, game_data)
    assert curve[0] < 0.10


def test_build_curve_full_grinding_high_success(game_data):
    # Zone 5, 10% required — only ancient_object/flower needed, which are harvestable
    curve = build_curve(3, 5, 0.1, 20, 500, game_data)
    assert curve[100] > 0.50
