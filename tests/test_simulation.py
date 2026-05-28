import numpy as np
import pytest
from data import load_data, ZoneData, MergeChain, ZoneUnlock
from simulation import simulate_harvest, check_puzzle_completion, check_zone_unlock, _compute_chain_base_units, simulate_player_run, build_full_run_results

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
                    composition={"item_1": 20.0})  # 2 tiles; need 2 free items
    assert check_puzzle_completion({"item_1": 1}, zone, 1.0, [chain]) is False


def test_lower_level_items_merge_up():
    chain = _make_chain(["item_1", "item_2", "item_3"])
    # 2 tiles of item_2. Each costs 1× item_2. Build item_2 from item_1: 3→1.
    # Tile 1: 3 item_1 → 1 item_2, pay 1. Remaining: 3 item_1 (for tile 2).
    # Tile 2: 3 item_1 → 1 item_2, pay 1. Total: 6 item_1 needed.
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=10,
                    composition={"item_2": 20.0})  # 2 tiles
    assert check_puzzle_completion({"item_1": 6}, zone, 1.0, [chain]) is True
    assert check_puzzle_completion({"item_1": 5}, zone, 1.0, [chain]) is False


def test_higher_level_cannot_downgrade():
    chain = _make_chain(["item_1", "item_2", "item_3"])
    # Need 2x item_1. Have only item_3 (cannot downgrade). Fail.
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=10,
                    composition={"item_1": 20.0})  # 2 tiles
    assert check_puzzle_completion({"item_3": 10}, zone, 1.0, [chain]) is False


def test_partial_completion():
    chain = _make_chain(["item_1", "item_2"])
    # 4 tiles of item_1; cost 1 per tile. 2 items → 50%, 4 items → 100%.
    zone = ZoneData(zone_id=99, zone_type="Puzzle", tile_count=20,
                    composition={"item_1": 20.0})  # 4 tiles
    assert check_puzzle_completion({"item_1": 4}, zone, 0.5, [chain]) is True
    assert check_puzzle_completion({"item_1": 3}, zone, 1.0, [chain]) is False


# --- check_zone_unlock tests ---


def test_no_unlock_required_passes():
    unlock = ZoneUnlock(zone_id=3, required_currency_level=None)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({}, unlock, cc) is True


def test_unlock_with_exact_currency():
    # Discrete 5→2/3→1 merge: minimum 21 Currency_1 to produce 1 Currency_4
    # (5C1→2C2, 5C1→2C2, 3C1→1C2 = 13C1→5C2; 5C1→2C2, 3C1→1C2 = 8C1→3C2; 3C2→1C3×3; 3C3→1C4)
    unlock = ZoneUnlock(zone_id=2, required_currency_level=4)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({"Event_LakeCottage_Currency_1": 21}, unlock, cc) is True


def test_unlock_insufficient_currency():
    # 20 Currency_1 is one short of minimum needed for discrete merge to Currency_4
    unlock = ZoneUnlock(zone_id=2, required_currency_level=4)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({"Event_LakeCottage_Currency_1": 20}, unlock, cc) is False


def test_unlock_with_higher_level_currency():
    unlock = ZoneUnlock(zone_id=2, required_currency_level=4)
    cc = _make_chain([f"Event_LakeCottage_Currency_{i}" for i in range(1, 11)])
    assert check_zone_unlock({"Event_LakeCottage_Currency_4": 1}, unlock, cc) is True


def test_compute_chain_base_units_single_item():
    chain = MergeChain(name="test", items=["a_0", "a_1", "a_2"])
    result = _compute_chain_base_units({"a_0": 2, "a_2": 1}, [chain])
    # a_0 at level 0: 2 × 3^0 = 2; a_2 at level 2: 1 × 3^2 = 9; total under chain_key "a_0" = 11
    assert result == {"a_0": 11}


def test_compute_chain_base_units_two_chains():
    chain_a = MergeChain(name="A", items=["a_0", "a_1"])
    chain_b = MergeChain(name="B", items=["b_0", "b_1"])
    result = _compute_chain_base_units({"a_1": 1, "b_0": 3}, [chain_a, chain_b])
    assert result["a_0"] == 3   # 1 × 3^1
    assert result["b_0"] == 3   # 3 × 3^0


def test_compute_chain_base_units_ignores_non_chain_items():
    chain = MergeChain(name="test", items=["a_0", "a_1"])
    result = _compute_chain_base_units({"a_0": 2, "unrelated_item": 100}, [chain])
    assert "unrelated_item" not in result
    assert result.get("a_0", 0) == 2


def test_puzzle_extra_grind_tracked(game_data):
    rng = np.random.default_rng(seed=0)
    run = simulate_player_run(game_data, puzzle_completion_pct=0.5,
                               harvest_away_max_harvests=20, rng=rng)
    # extra_grind must be >= 0 for all puzzle zones with data
    for zone_id, extra in run.zone_puzzle_extra_grind.items():
        assert extra >= 0.0, f"Zone {zone_id} extra_grind is negative: {extra}"
        assert extra <= 1.0, f"Zone {zone_id} extra_grind > 1.0: {extra}"


def test_puzzle_zone_blockers_recorded():
    # Use full 3-file data so loot tables produce currency and players reach puzzle zones
    full_data = load_data(
        "data/Discovery Event Layout Generator 001.xlsx",
        "data/_Data_Loot - Event LakeCottage.xlsx",
        "data/_Data_Objects - Event LakeCottage.xlsx",
    )
    rng = np.random.default_rng(seed=7)
    # Use 100% completion — very hard, most chains will be blockers
    run = simulate_player_run(full_data, puzzle_completion_pct=1.0,
                               harvest_away_max_harvests=20, rng=rng)
    puzzle_zone_ids = {zid for zid, z in full_data.zones.items() if z.zone_type == "Puzzle"}
    blocker_keys = set(run.zone_blocker_map.keys())
    # At 100% completion requirement, puzzle zones should have blockers
    assert blocker_keys & puzzle_zone_ids, "Expected at least one puzzle zone to have blockers"


def test_puzzle_chain_sources_tracked():
    full_data = load_data(
        "data/Discovery Event Layout Generator 001.xlsx",
        "data/_Data_Loot - Event LakeCottage.xlsx",
        "data/_Data_Objects - Event LakeCottage.xlsx",
    )
    rng = np.random.default_rng(seed=3)
    run = simulate_player_run(full_data, puzzle_completion_pct=0.5,
                               harvest_away_max_harvests=20, rng=rng)
    # zone_puzzle_chain_sources should be populated for reached puzzle zones
    assert len(run.zone_puzzle_chain_sources) > 0, "Expected chain sources for at least one puzzle zone"
    for zone_id, chain_map in run.zone_puzzle_chain_sources.items():
        for chain_key, sources in chain_map.items():
            assert 'bramble' in sources and 'other' in sources, f"Zone {zone_id} chain {chain_key} missing source keys"
            assert sources['bramble'] >= 0
            assert sources['other'] >= 0




# --- build_full_run_results parallel tests ---

@pytest.fixture(scope="module")
def full_data():
    return load_data(
        "data/Discovery Event Layout Generator 001.xlsx",
        "data/_Data_Loot - Event LakeCottage.xlsx",
        "data/_Data_Objects - Event LakeCottage.xlsx",
    )


def test_parallel_build_returns_correct_player_count(full_data):
    result = build_full_run_results(full_data, 0.5, 3, n_players=40, n_workers=2)
    assert len(result.scores) == 40


def test_parallel_build_has_all_percentile_keys(full_data):
    result = build_full_run_results(full_data, 0.5, 3, n_players=20, n_workers=2)
    for key in ["p5", "p10", "p25", "p50", "p75", "p90", "p95"]:
        assert key in result.score_percentiles


def test_parallel_and_sequential_same_output_shape(full_data):
    r1 = build_full_run_results(full_data, 0.5, 3, n_players=30, n_workers=1)
    r2 = build_full_run_results(full_data, 0.5, 3, n_players=30, n_workers=3)
    assert len(r1.scores) == len(r2.scores)
    assert set(r1.zone_grind_pcts.keys()) == set(r2.zone_grind_pcts.keys())
