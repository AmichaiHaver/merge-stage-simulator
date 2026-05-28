from __future__ import annotations
import math
import multiprocessing
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from data import GameData, ZoneData, MergeChain, ZoneUnlock


HARVEST_AWAY_PREFIX = "Event_LakeCottage_HarvestAway_"
CURRENCY_MERGE_RATIO = 2.5  # 5 currency items → 2 at next level
_PCT_STEPS = [p / 100 for p in range(5, 105, 5)]  # 0.05 .. 1.00

# Life Orb level → healing power (Life_Orb_N_Root → level N)
LIFE_ORB_HEALING_POWER = {
    0: 1, 1: 4, 2: 16, 3: 64, 4: 256,
    5: 1024, 6: 4096, 7: 16_384, 8: 65_536, 9: 327_680,
}


def _compute_healing_power(inventory: dict[str, int]) -> float:
    total = 0.0
    for prefab, count in inventory.items():
        if prefab.startswith("Life_Orb_") and prefab.endswith("_Root"):
            parts = prefab.split("_")
            if len(parts) >= 3:
                try:
                    level = int(parts[2])
                    total += count * LIFE_ORB_HEALING_POWER.get(level, 0)
                except ValueError:
                    pass
    return total


def get_harvest_away_max_harvests(data: GameData) -> int:
    """Read HarvestAway max_harvests from game data (Harvest Charges column)."""
    for prefab, plant in data.plants.items():
        if HARVEST_AWAY_PREFIX in prefab:
            return plant.max_harvests
    return 20


def _harvest_away_base_currency_level(zone_id: int) -> int:
    """Zones 1-3 → base 1, zones 4-6 → base 2, zones 7-9 → base 3."""
    if zone_id <= 3:
        return 1
    elif zone_id <= 6:
        return 2
    return 3


def _get_harvest_away_cascade(prefab: str, plants: dict) -> list[str]:
    """For HarvestAway_N (numeric suffix), return [HarvestAway_{N-1}, ..., HarvestAway_1] in plants."""
    last_underscore = prefab.rfind("_")
    if last_underscore == -1:
        return []
    suffix = prefab[last_underscore + 1:]
    if not suffix.isdigit():
        return []
    level = int(suffix)
    base = prefab[:last_underscore + 1]
    return [f"{base}{lvl}" for lvl in range(level - 1, 0, -1) if f"{base}{lvl}" in plants]


def _apply_plant_to_inventory(
    plant,
    n_items: int,
    loot_tables: dict,
    inventory: dict,
    rng: np.random.Generator,
) -> None:
    lt = loot_tables.get(plant.loot_table_name)
    total_rolls = n_items * plant.max_harvests
    if lt:
        names = [lt.default_item] + [e.item for e in lt.entries]
        probs = [lt.default_probability] + [e.probability for e in lt.entries]
        total = sum(probs)
        if total > 0:
            probs_arr = np.array(probs, dtype=float) / total
            chosen = rng.choice(len(names), size=total_rolls, p=probs_arr)
            for idx in chosen:
                inventory[names[idx]] += 1
    elif plant.loot_table_name:
        inventory[plant.loot_table_name] += total_rolls

    if plant.on_die:
        inventory[plant.on_die] += n_items


def _compute_zone_harvest_seconds(zone: ZoneData, grinding_pct: float, data: GameData) -> float:
    """Total harvest seconds for one zone (single dragon). Cascade only for LakeCottage HarvestAway."""
    total = 0.0
    for prefab, zone_pct in zone.composition.items():
        n_items = round((zone_pct / 100) * zone.tile_count * grinding_pct)
        if n_items == 0:
            continue
        if prefab in data.plants:
            cascade = _get_harvest_away_cascade(prefab, data.plants) if prefab.startswith(HARVEST_AWAY_PREFIX) else []
            all_prefabs = [prefab] + cascade
            for p_name in all_prefabs:
                plant = data.plants[p_name]
                total += n_items * plant.max_harvests * plant.harvest_time
    return total


def _zone_harvest_capacity(zone: ZoneData, data: GameData) -> float:
    """Total possible harvest charges in a zone at 100% grind.

    Counts HarvestAway and Bramble tiles only. LakeCottage HarvestAway_N
    includes cascade charges (_N + _N-1 + ... + _1). Other HarvestAway tiles
    do not cascade.
    """
    total = 0.0
    for prefab, zone_pct in zone.composition.items():
        n_tiles = round((zone_pct / 100) * zone.tile_count)
        if n_tiles == 0:
            continue
        if "HarvestAway" not in prefab and "Brambles" not in prefab:
            continue
        if prefab not in data.plants:
            continue
        if prefab.startswith(HARVEST_AWAY_PREFIX):
            cascade = _get_harvest_away_cascade(prefab, data.plants)
            all_prefabs = [prefab] + cascade
            for p_name in all_prefabs:
                if p_name in data.plants:
                    total += n_tiles * data.plants[p_name].max_harvests
        else:
            total += n_tiles * data.plants[prefab].max_harvests
    return total


def simulate_harvest(
    grindy_zone: ZoneData,
    grinding_pct: float,             # 0.0–1.0
    harvest_away_max_harvests: int,  # fallback for items not in data.plants
    data: GameData,
    rng: np.random.Generator,
) -> dict[str, int]:
    inventory: dict[str, int] = defaultdict(int)

    for prefab, zone_pct in grindy_zone.composition.items():
        n_items = round((zone_pct / 100) * grindy_zone.tile_count * grinding_pct)
        if n_items == 0:
            continue

        if prefab in data.plants:
            # Cascade only for LakeCottage HarvestAway discovery items
            cascade = _get_harvest_away_cascade(prefab, data.plants) if prefab.startswith(HARVEST_AWAY_PREFIX) else []
            all_prefabs = [prefab] + cascade
            for proc_prefab in all_prefabs:
                _apply_plant_to_inventory(
                    data.plants[proc_prefab], n_items, data.loot_tables, inventory, rng
                )

        elif prefab.startswith(HARVEST_AWAY_PREFIX):
            # Fallback for old-format HarvestAway items not in plants dict
            base = _harvest_away_base_currency_level(grindy_zone.zone_id)
            levels = [base, base + 1, base + 2]
            currency_items = [
                data.currency_chain.items[lvl - 1]
                for lvl in levels
                if lvl - 1 < len(data.currency_chain.items)
            ]
            if currency_items:
                total_harvests = n_items * harvest_away_max_harvests
                chosen = rng.integers(0, len(currency_items), size=total_harvests)
                for idx in chosen:
                    inventory[currency_items[idx]] += 1

        elif prefab.startswith("PlainGrass"):
            # 3 identical grass tiles merge → 1 level-1 point item
            if data.points_chain.items:
                inventory[data.points_chain.items[0]] += n_items // 3

        elif (prefab in data.currency_chain.items
              or prefab in data.points_chain.items):
            # Currency / point items placed directly on the board — pick up as-is
            inventory[prefab] += n_items

    return dict(inventory)


def _try_build_n_currency(
    inv: dict[str, int],
    currency_chain: MergeChain,
    level: int,
    n: int,
) -> dict[str, int] | None:
    """Build n items of currency_chain.items[level] via discrete merging.

    Merge rules (matching game mechanics):
      5 of level-1 → 2 of level  (efficient, use when need ≥2)
      3 of level-1 → 1 of level  (single, use when need 1)
    Never splits higher-level items downward.
    Returns new inventory dict or None if impossible.
    """
    items = currency_chain.items
    if level < 0 or level >= len(items):
        return None

    result = dict(inv)
    still_need = n - result.get(items[level], 0)
    if still_need <= 0:
        return result
    if level == 0:
        return None

    lower = items[level - 1]
    upper = items[level]

    while still_need > 0:
        if still_need >= 2:
            sub = _try_build_n_currency(result, currency_chain, level - 1, 5)
            if sub is not None:
                result = sub
                result[lower] = result.get(lower, 0) - 5
                result[upper] = result.get(upper, 0) + 2
                still_need -= 2
                continue
        # Need 1 (or couldn't build 5): use 3→1
        sub = _try_build_n_currency(result, currency_chain, level - 1, 3)
        if sub is None:
            return None
        result = sub
        result[lower] = result.get(lower, 0) - 3
        result[upper] = result.get(upper, 0) + 1
        still_need -= 1

    return result


def check_zone_unlock(
    inventory: dict[str, int],
    zone_unlock: ZoneUnlock,
    currency_chain: MergeChain,
) -> bool:
    if zone_unlock.required_currency_level is None:
        return True
    k = zone_unlock.required_currency_level
    level_idx = k - 1
    if level_idx >= len(currency_chain.items):
        return False
    if inventory.get(currency_chain.items[level_idx], 0) >= 1:
        return True
    return _try_build_n_currency(inventory, currency_chain, level_idx, 1) is not None


def _apply_currency_merge(
    inventory: dict[str, int],
    zone_unlock: ZoneUnlock,
    currency_chain: MergeChain,
) -> dict[str, int]:
    """Merge lower-level currency items to produce the required unlock level.
    Returns updated inventory (items stay — not consumed on unlock).
    """
    if zone_unlock.required_currency_level is None:
        return inventory
    level_idx = zone_unlock.required_currency_level - 1
    if level_idx >= len(currency_chain.items):
        return inventory
    if inventory.get(currency_chain.items[level_idx], 0) >= 1:
        return inventory
    merged = _try_build_n_currency(inventory, currency_chain, level_idx, 1)
    return merged if merged is not None else inventory


def _add_inventories(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    result = dict(a)
    for k, v in b.items():
        result[k] = result.get(k, 0) + v
    return result


def _compute_score(
    inventory: dict[str, int],
    point_values: dict[str, int],
    points_chain: "MergeChain | None" = None,
) -> float:
    if points_chain is None or not points_chain.items:
        return sum(inventory.get(prefab, 0) * val for prefab, val in point_values.items())

    # Greedy merge: 5 of level i → 2 of level i+1, bottom-up
    merged: dict[str, int] = {p: inventory.get(p, 0) for p in points_chain.items}
    items = points_chain.items
    for i in range(len(items) - 1):
        count = merged.get(items[i], 0)
        merges = count // 5
        if merges > 0:
            merged[items[i]] = count - merges * 5
            merged[items[i + 1]] = merged.get(items[i + 1], 0) + merges * 2

    return sum(merged.get(prefab, 0) * val for prefab, val in point_values.items())


def _downstream_puzzle_ids(grindy_zone_id: int, zones: dict[int, ZoneData]) -> list[int]:
    """The first puzzle zone immediately after grindy_zone_id (the direct gate)."""
    for zid in range(grindy_zone_id + 1, max(zones.keys()) + 1):
        z = zones.get(zid)
        if z is None or z.zone_type == "Grindy":
            break
        if z.zone_type == "Puzzle":
            return [zid]
    return []


# ── Puzzle unlock mechanics ───────────────────────────────────────────────────

def _try_build_n_items(
    inv: dict[str, int],
    chain: MergeChain,
    level: int,
    n_needed: int,
) -> "dict[str, int] | None":
    """
    Try to produce n_needed items of chain.items[level] by merging from lower levels.
    Uses 5→2 when building 2+ items (more efficient), 3→1 when building 1.
    Returns a NEW dict with modifications on success, None on failure.
    Input inv is never modified.
    """
    available = inv.get(chain.items[level], 0)
    if available >= n_needed:
        return dict(inv)  # already have enough; caller may modify the returned copy

    if level == 0:
        return None  # can't merge up from below

    work = dict(inv)
    still_need = n_needed - available

    while still_need > 0:
        built = False
        if still_need >= 2:
            # Try 5→2 (5 of level-1 → 2 of this level)
            result = _try_build_n_items(work, chain, level - 1, 5)
            if result is not None:
                result[chain.items[level - 1]] = result.get(chain.items[level - 1], 0) - 5
                result[chain.items[level]] = result.get(chain.items[level], 0) + 2
                work = result
                still_need -= 2
                built = True

        if not built:
            # Try 3→1 (3 of level-1 → 1 of this level)
            result = _try_build_n_items(work, chain, level - 1, 3)
            if result is None:
                return None
            result[chain.items[level - 1]] = result.get(chain.items[level - 1], 0) - 3
            result[chain.items[level]] = result.get(chain.items[level], 0) + 1
            work = result
            still_need -= 1

    return work


def attempt_puzzle_zone(
    inv: dict[str, int],
    puzzle_zone: ZoneData,
    chains: list[MergeChain],
) -> tuple[int, dict[str, int], dict[str, int]]:
    """
    Try to unlock as many Competition_* tiles as possible.

    Mechanic per tile at level L:
      - Spend 1× level-L item from inventory (build from lower if needed)
      - Freed board tile goes into a merge-only pool (cannot unlock same-level tiles)
      - When freed + free items at level L reach 5: 5→2 merge to L+1 (preferred)
      - When freed + free items at level L reach 3: 3→1 merge to L+1
      - Merged items (L+1) are free to use for unlocking L+1 tiles

    Tiles sorted lowest level first. Cascades: merges may enable higher-level unlocks.

    Modifies inv in-place with net changes.
    Returns (opened_count, chain_opened, chain_total).
    """
    # Collect Competition_* tiles by chain
    puzzle_items: list[tuple[int, MergeChain, str]] = []
    chain_total: dict[str, int] = {}

    for prefab, zone_pct in puzzle_zone.composition.items():
        count = max(1, round((zone_pct / 100) * puzzle_zone.tile_count))
        if count == 0:
            continue
        for chain in chains:
            if prefab in chain.items:
                level = chain.items.index(prefab)
                chain_key = chain.items[0]
                puzzle_items.extend([(level, chain, chain_key)] * count)
                chain_total[chain_key] = chain_total.get(chain_key, 0) + count
                break

    if not puzzle_items:
        return 0, {}, {}

    puzzle_items.sort(key=lambda x: x[0])  # lowest level first
    chain_opened: dict[str, int] = {k: 0 for k in chain_total}
    opened_count = 0
    remaining = list(puzzle_items)
    free = dict(inv)       # items usable for unlocking tiles
    freed: dict[str, int] = {}  # freed board tiles: can merge but not unlock same level

    made_progress = True
    while made_progress:
        made_progress = False

        # Unlock phase: spend 1 free item per tile
        next_remaining: list[tuple[int, MergeChain, str]] = []
        for level, chain, chain_key in remaining:
            result = _try_build_n_items(free, chain, level, 1)
            if result is not None:
                result[chain.items[level]] = result.get(chain.items[level], 0) - 1
                free = result
                item = chain.items[level]
                freed[item] = freed.get(item, 0) + 1
                chain_opened[chain_key] = chain_opened.get(chain_key, 0) + 1
                opened_count += 1
                made_progress = True
            else:
                next_remaining.append((level, chain, chain_key))
        remaining = next_remaining

        # Merge phase: merge (free + freed) at each level using 5→2 then 3→1
        # Merged items are free (can unlock at higher level)
        for chain in chains:
            for L in range(len(chain.items) - 1):
                item_L = chain.items[L]
                item_L1 = chain.items[L + 1]
                total = free.get(item_L, 0) + freed.get(item_L, 0)

                # 5→2 (preferred merge ratio)
                fives = total // 5
                if fives > 0:
                    consume = fives * 5
                    freed_use = min(freed.get(item_L, 0), consume)
                    freed[item_L] = freed.get(item_L, 0) - freed_use
                    free[item_L] = free.get(item_L, 0) - (consume - freed_use)
                    free[item_L1] = free.get(item_L1, 0) + fives * 2
                    total -= consume
                    made_progress = True

                # 3→1 with remainder
                threes = total // 3
                if threes > 0:
                    consume = threes * 3
                    freed_use = min(freed.get(item_L, 0), consume)
                    freed[item_L] = freed.get(item_L, 0) - freed_use
                    free[item_L] = free.get(item_L, 0) - (consume - freed_use)
                    free[item_L1] = free.get(item_L1, 0) + threes
                    made_progress = True

    # Commit: both free and freed items remain in inventory
    inv.clear()
    for item, count in free.items():
        if count > 0:
            inv[item] = count
    for item, count in freed.items():
        if count > 0:
            inv[item] = inv.get(item, 0) + count
    return opened_count, chain_opened, chain_total


def _count_openable_tiles_analytical(
    inventory: dict[str, int],
    puzzle_zone: ZoneData,
    chains: list[MergeChain],
) -> tuple[int, dict[str, int], dict[str, int]]:
    """O(chains × levels) equivalent of attempt_puzzle_zone for read-only checks.

    Does NOT modify inventory. Process each chain bottom-up: merge surplus from
    level-1 (5→2 then 3→1), open tiles (cost 1 per tile, net-zero — freed tile
    returns to pool). No refund upward from opens; carry is via normal merges.
    """
    chain_total: dict[str, int] = {}
    chain_tile_counts: dict[str, dict[int, int]] = {}
    chain_objects: dict[str, MergeChain] = {}

    for prefab, zone_pct in puzzle_zone.composition.items():
        count = max(1, round((zone_pct / 100) * puzzle_zone.tile_count))
        if count == 0:
            continue
        for chain in chains:
            if prefab in chain.items:
                level = chain.items.index(prefab)
                chain_key = chain.items[0]
                chain_total[chain_key] = chain_total.get(chain_key, 0) + count
                if chain_key not in chain_tile_counts:
                    chain_tile_counts[chain_key] = {}
                    chain_objects[chain_key] = chain
                chain_tile_counts[chain_key][level] = chain_tile_counts[chain_key].get(level, 0) + count
                break

    if not chain_tile_counts:
        return 0, {}, {}

    chain_opened: dict[str, int] = {}
    total_opened = 0

    for chain_key, tile_counts in chain_tile_counts.items():
        chain = chain_objects[chain_key]
        n_levels = len(chain.items)
        surplus = [inventory.get(chain.items[L], 0) for L in range(n_levels)]

        opened_this_chain = 0
        for L in range(n_levels):
            if L > 0 and surplus[L - 1] > 0:
                fives = surplus[L - 1] // 5
                surplus[L] += fives * 2
                surplus[L - 1] -= fives * 5
                threes = surplus[L - 1] // 3
                surplus[L] += threes
                surplus[L - 1] -= threes * 3

            tiles_at_L = tile_counts.get(L, 0)
            if tiles_at_L > 0:
                # Cost 1 per tile; freed tile returns to pool → net-zero on surplus[L]
                openable = min(tiles_at_L, surplus[L])
                opened_this_chain += openable
                # No surplus[L] reduction (freed tile replaces paid item)
                # No refund to surplus[L+1]; carry happens via build step at L+1

        chain_opened[chain_key] = opened_this_chain
        total_opened += opened_this_chain

    return total_opened, chain_opened, chain_total


def _get_bottleneck_chain(
    chain_opened: dict[str, int],
    chain_total: dict[str, int],
) -> "str | None":
    """Chain with the lowest relative completion rate = hardest to progress = bottleneck."""
    if not chain_total:
        return None
    min_rate = float("inf")
    bottleneck: "str | None" = None
    for key, total in chain_total.items():
        rate = chain_opened.get(key, 0) / total if total > 0 else 1.0
        if rate < min_rate:
            min_rate = rate
            bottleneck = key
    return bottleneck


def check_puzzle_completion(
    inventory: dict[str, int],
    puzzle_zone: ZoneData,
    required_merge_pct: float,
    chains: list[MergeChain],
) -> bool:
    """Compatibility wrapper: uses the correct attempt_puzzle_zone mechanic."""
    inv_copy = dict(inventory)
    opened, _, chain_total = attempt_puzzle_zone(inv_copy, puzzle_zone, chains)
    total = sum(chain_total.values())
    if total == 0:
        return True
    return opened >= math.ceil(total * required_merge_pct)


def _chain_display_name(chain_key: str) -> str:
    """Competition_ancient_object_1 → ancient_object, Competition_Crystal_0 → Crystal"""
    s = chain_key
    if s.startswith("Competition_"):
        s = s[len("Competition_"):]
    parts = s.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        s = parts[0]
    return s


def _compute_chain_base_units(items: dict[str, int], chains: list[MergeChain]) -> dict[str, int]:
    """Sum base units per chain. base_units(item at level L) = count × 3^L."""
    result: dict[str, int] = {}
    for prefab, count in items.items():
        if count <= 0:
            continue
        for chain in chains:
            if prefab in chain.items:
                level = chain.items.index(prefab)
                chain_key = chain.items[0]
                result[chain_key] = result.get(chain_key, 0) + count * (3 ** level)
                break
    return result


# ── Full end-to-end player simulation ────────────────────────────────────────

@dataclass
class FullRunResult:
    scores: list[float]
    score_percentiles: dict[str, float]           # "p5" .. "p95"
    zone_grind_pcts: dict[int, list[float]]       # grindy_zone_id → grinding % per player
    zone_scores: dict[int, list[float]]           # zone_id → cumulative score per player
    zone_healing_power: dict[int, list[float]]     # zone_id → cumulative healing power per player
    zone_max_healing_power: dict[int, list[float]] # zone_id → healing if 100% grind every grindy zone
    zone_chain_blockers: dict[int, dict[str, int]]
    # grindy zone → {chain_display_name: player_count}
    # 'Currency' means zone unlock was the blocker; chain name means puzzle chain was
    zone_puzzle_extra_grind: dict[int, list[float]]
    # puzzle_zone_id → per-player distribution of (grinding_pct - discovery_only_pct)
    zone_puzzle_chain_sources: dict[int, dict[str, dict[str, list[float]]]]
    # puzzle_zone_id → chain_key → {'bramble': [per-player base units], 'other': [per-player base units]}
    zone_harvest_seconds: dict[int, list[float]]
    # zone_id → per-player harvest seconds (single dragon)
    zone_harvest_efficiency: dict[int, list[float]]
    # zone_id → per-player (cumulative_actual / cumulative_possible) up to this zone
    zone_item_counts: dict[int, dict[str, list[int]]]
    # zone_id → item_prefab → per-player count at end of zone (currency + puzzle chain items)


@dataclass
class PlayerRunResult:
    final_score: float
    grinding_per_zone: dict[int, float]
    completed_zones: set[int]
    zone_score_map: dict[int, float]
    zone_healing_map: dict[int, float]
    zone_max_healing_map: dict[int, float]       # cumulative healing if 100% grind every grindy zone
    zone_blocker_map: dict[int, list[str]]       # zone_id → list of chain display names
    zone_puzzle_extra_grind: dict[int, float]    # puzzle_zone_id → (grinding_pct - discovery_only_pct)
    zone_puzzle_chain_sources: dict[int, dict[str, dict[str, int]]]
    # puzzle_zone_id → chain_key → {'bramble': base_units, 'other': base_units}
    zone_harvest_seconds: dict[int, float]       # zone_id → harvest seconds (single dragon)
    zone_inventory_snapshot: dict[int, dict[str, int]]
    # zone_id → {item_prefab: count} at end of zone (currency + puzzle chain items only)
    zone_harvest_efficiency: dict[int, float]
    # zone_id → cumulative_actual_harvests / cumulative_possible_harvests up to this zone


def simulate_player_run(
    data: GameData,
    puzzle_completion_pct: float,
    harvest_away_max_harvests: int,
    rng: np.random.Generator,
) -> PlayerRunResult:
    """
    Simulate one player running the full event end-to-end.

    Returns a PlayerRunResult dataclass.
    zone_blocker_map: grindy zone_id → list of chain display names or ['Currency']
    """
    inventory: dict[str, int] = {}
    max_inv: dict[str, int] = {}  # 100% grind in every grindy zone
    grinding_per_zone: dict[int, float] = {}
    completed_zones: set[int] = set()
    zone_score_map: dict[int, float] = {}
    zone_healing_map: dict[int, float] = {}
    zone_max_healing_map: dict[int, float] = {}
    zone_blocker_map: dict[int, list[str]] = {}
    zone_puzzle_extra_grind: dict[int, float] = {}
    zone_puzzle_chain_sources: dict[int, dict[str, dict[str, int]]] = {}
    zone_harvest_seconds_map: dict[int, float] = {}
    zone_inventory_snapshot_map: dict[int, dict[str, int]] = {}
    zone_harvest_efficiency_map: dict[int, float] = {}
    # Precompute possible harvest capacity per zone (constant across players)
    zone_capacity: dict[int, float] = {
        zid: _zone_harvest_capacity(z, data) for zid, z in data.zones.items()
    }
    # Precompute cumulative possible harvests up to each zone (denominator for efficiency)
    sorted_zone_ids = sorted(data.zones.keys())
    cumulative_possible: dict[int, float] = {}
    running = 0.0
    for zid in sorted_zone_ids:
        running += zone_capacity.get(zid, 0.0)
        cumulative_possible[zid] = running
    cumulative_actual_harvests: float = 0.0
    # Puzzle zone non-Competition harvest pre-simulated during grindy zone processing
    cached_puzzle_harvests: dict[int, dict[str, int]] = {}

    made_progress = True
    while made_progress:
        made_progress = False
        for zone_id in sorted(data.zones.keys()):
            if zone_id in completed_zones:
                continue
            zone = data.zones[zone_id]
            unlock = data.zone_unlocks.get(zone_id, ZoneUnlock(zone_id, None))

            if not check_zone_unlock(inventory, unlock, data.currency_chain):
                continue

            # Physically merge currency items to produce the required unlock level
            inventory = _apply_currency_merge(inventory, unlock, data.currency_chain)

            if zone.zone_type == "Grindy":
                downstream = _downstream_puzzle_ids(zone_id, data.zones)

                # Pre-simulate full grindy harvest once; scale for stepping
                full_grindy_inv = simulate_harvest(zone, 1.0, harvest_away_max_harvests, data, rng)
                full_grindy_100 = full_grindy_inv  # preserved for max-healing tracking

                # Pre-simulate downstream puzzle zone non-Competition harvest (once, cached)
                for pz_id in downstream:
                    if pz_id in data.zones and pz_id not in cached_puzzle_harvests:
                        cached_puzzle_harvests[pz_id] = simulate_harvest(
                            data.zones[pz_id], 1.0, harvest_away_max_harvests, data, rng
                        )

                # Find minimum grind for discovery (zone unlock) only — no puzzle completion check
                discovery_only_pct = 1.0
                if downstream:
                    for step in range(1, 21):
                        pct = step / 20
                        grindy_slice = {k: round(v * pct) for k, v in full_grindy_inv.items()}
                        test_inv = _add_inventories(inventory, grindy_slice)
                        if all(
                            check_zone_unlock(
                                test_inv,
                                data.zone_unlocks.get(pz_id, ZoneUnlock(pz_id, None)),
                                data.currency_chain,
                            )
                            for pz_id in downstream
                            if pz_id in data.zones
                        ):
                            discovery_only_pct = pct
                            break

                # Find minimum grinding % that lets the player complete downstream puzzle zone
                grinding_pct = 1.0
                for step in range(1, 21):
                    pct = step / 20
                    grindy_slice = {k: round(v * pct) for k, v in full_grindy_inv.items()}
                    test_inv = _add_inventories(inventory, grindy_slice)
                    for pz_id in downstream:
                        if pz_id in cached_puzzle_harvests:
                            test_inv = _add_inventories(test_inv, cached_puzzle_harvests[pz_id])

                    all_ok = bool(downstream)
                    for pz_id in downstream:
                        if pz_id not in data.zones:
                            continue
                        pz = data.zones[pz_id]
                        if not check_zone_unlock(
                            test_inv,
                            data.zone_unlocks.get(pz_id, ZoneUnlock(pz_id, None)),
                            data.currency_chain,
                        ):
                            all_ok = False
                            break
                        opened, _, chain_total = _count_openable_tiles_analytical(test_inv, pz, data.chains)
                        total_tiles = sum(chain_total.values())
                        required = math.ceil(total_tiles * puzzle_completion_pct) if total_tiles > 0 else 0
                        if opened < required:
                            all_ok = False
                            break

                    if all_ok:
                        grinding_pct = pct
                        full_grindy_inv = grindy_slice
                        break
                else:
                    # Even 100% grind is insufficient — record which chain is the bottleneck
                    if downstream:
                        final_test = _add_inventories(inventory, full_grindy_inv)
                        for pz_id in downstream:
                            if pz_id in cached_puzzle_harvests:
                                final_test = _add_inventories(final_test, cached_puzzle_harvests[pz_id])
                        for pz_id in downstream:
                            if pz_id not in data.zones:
                                continue
                            pz = data.zones[pz_id]
                            if not check_zone_unlock(
                                final_test,
                                data.zone_unlocks.get(pz_id, ZoneUnlock(pz_id, None)),
                                data.currency_chain,
                            ):
                                zone_blocker_map[zone_id] = ["Currency"]
                                break
                            opened, chain_opened, chain_total = _count_openable_tiles_analytical(
                                final_test, pz, data.chains
                            )
                            total_tiles = sum(chain_total.values())
                            required = math.ceil(total_tiles * puzzle_completion_pct) if total_tiles > 0 else 0
                            if opened < required:
                                bottleneck = _get_bottleneck_chain(chain_opened, chain_total)
                                zone_blocker_map[zone_id] = [
                                    _chain_display_name(bottleneck) if bottleneck else "puzzle"
                                ]
                                break

                # Store extra grind (puzzle completion cost beyond discovery) per downstream puzzle zone
                extra = max(0.0, grinding_pct - discovery_only_pct)
                for pz_id in downstream:
                    zone_puzzle_extra_grind[pz_id] = extra

                inventory = _add_inventories(inventory, full_grindy_inv)
                max_inv = _add_inventories(max_inv, full_grindy_100)
                grinding_per_zone[zone_id] = grinding_pct

            else:
                # Non-grindy zone (Start / Puzzle / Fog):
                # Snapshot chain base units BEFORE adding puzzle zone's own harvest
                pre_puzzle_chain_units = _compute_chain_base_units(inventory, data.chains) if data.chains else {}

                # Apply pre-simulated harvest if available, otherwise simulate now
                if zone_id in cached_puzzle_harvests:
                    inv_delta = cached_puzzle_harvests.pop(zone_id)
                else:
                    inv_delta = simulate_harvest(zone, 1.0, harvest_away_max_harvests, data, rng)

                bramble_chain_units = _compute_chain_base_units(inv_delta, data.chains) if data.chains else {}
                inventory = _add_inventories(inventory, inv_delta)
                max_inv = _add_inventories(max_inv, inv_delta)

                # For puzzle zones: attempt to unlock Competition_* tiles
                if zone.zone_type == "Puzzle" and data.chains:
                    opened, chain_opened, chain_total = attempt_puzzle_zone(inventory, zone, data.chains)

                    blockers: list[str] = []
                    for chain_key, total in chain_total.items():
                        per_chain_required = math.ceil(total * puzzle_completion_pct) if total > 0 else 0
                        if chain_opened.get(chain_key, 0) < per_chain_required:
                            blockers.append(_chain_display_name(chain_key))

                    # Check discovery: does the player have the Currency unlock for the next zone?
                    next_zone_id = zone_id + 1
                    while next_zone_id in data.zones and data.zones[next_zone_id].zone_type not in ("Grindy", "Puzzle"):
                        next_zone_id += 1
                    if next_zone_id in data.zones:
                        next_unlock = data.zone_unlocks.get(next_zone_id, ZoneUnlock(next_zone_id, None))
                        if not check_zone_unlock(inventory, next_unlock, data.currency_chain):
                            if "Currency" not in blockers:
                                blockers.append("Currency")

                    if blockers:
                        zone_blocker_map[zone_id] = blockers

                    # Record chain source breakdown (bramble harvest vs prior zones)
                    if chain_total:
                        sources: dict[str, dict[str, int]] = {}
                        for chain_key in chain_total:
                            sources[chain_key] = {
                                'bramble': bramble_chain_units.get(chain_key, 0),
                                'other': pre_puzzle_chain_units.get(chain_key, 0),
                            }
                        zone_puzzle_chain_sources[zone_id] = sources

            _grind_pct = grinding_per_zone.get(zone_id, 1.0)
            zone_harvest_seconds_map[zone_id] = _compute_zone_harvest_seconds(zone, _grind_pct, data)

            # Harvest efficiency: actual / possible cumulative up to this zone
            _actual_pct = _grind_pct if zone.zone_type == "Grindy" else 1.0
            cumulative_actual_harvests += _actual_pct * zone_capacity.get(zone_id, 0.0)
            _cum_possible = cumulative_possible.get(zone_id, 0.0)
            if _cum_possible > 0:
                zone_harvest_efficiency_map[zone_id] = cumulative_actual_harvests / _cum_possible

            completed_zones.add(zone_id)
            made_progress = True
            zone_score_map[zone_id] = _compute_score(inventory, data.point_values, data.points_chain)
            zone_healing_map[zone_id] = _compute_healing_power(inventory)
            zone_max_healing_map[zone_id] = _compute_healing_power(max_inv)
            _tracked = set(data.currency_chain.items)
            for _ch in data.chains:
                _tracked.update(_ch.items)
            zone_inventory_snapshot_map[zone_id] = {p: inventory.get(p, 0) for p in _tracked}

    final_score = _compute_score(inventory, data.point_values, data.points_chain)
    return PlayerRunResult(
        final_score=final_score,
        grinding_per_zone=grinding_per_zone,
        completed_zones=completed_zones,
        zone_score_map=zone_score_map,
        zone_healing_map=zone_healing_map,
        zone_max_healing_map=zone_max_healing_map,
        zone_blocker_map=zone_blocker_map,
        zone_puzzle_extra_grind=zone_puzzle_extra_grind,
        zone_puzzle_chain_sources=zone_puzzle_chain_sources,
        zone_harvest_seconds=zone_harvest_seconds_map,
        zone_inventory_snapshot=zone_inventory_snapshot_map,
        zone_harvest_efficiency=zone_harvest_efficiency_map,
    )


def _run_batch(args: tuple) -> list[PlayerRunResult]:
    data, puzzle_completion_pct, harvest_away_max_harvests, n, seed = args
    rng = np.random.default_rng(seed)
    return [
        simulate_player_run(data, puzzle_completion_pct, harvest_away_max_harvests, rng)
        for _ in range(n)
    ]


def build_full_run_results(
    data: GameData,
    puzzle_completion_pct: float,
    harvest_away_max_harvests: int,
    n_players: int,
    n_workers: Optional[int] = None,
) -> FullRunResult:
    effective_workers = n_workers if n_workers is not None else (os.cpu_count() or 1)
    # Ensure ≥200 players per worker to amortize process-spawn overhead (~0.07s/worker)
    effective_workers = max(1, min(effective_workers, n_players // 200))

    base = n_players // effective_workers
    remainder = n_players % effective_workers
    batch_sizes = [base + (1 if i < remainder else 0) for i in range(effective_workers)]

    ss = np.random.SeedSequence()
    seeds = [int(s.generate_state(1)[0]) for s in ss.spawn(effective_workers)]
    args_list = [
        (data, puzzle_completion_pct, harvest_away_max_harvests, n, seed)
        for n, seed in zip(batch_sizes, seeds)
    ]

    if effective_workers == 1:
        all_runs: list[PlayerRunResult] = _run_batch(args_list[0])
    else:
        _fork_ctx = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(max_workers=effective_workers, mp_context=_fork_ctx) as pool:
            batches = list(pool.map(_run_batch, args_list))
        all_runs = [run for batch in batches for run in batch]

    scores: list[float] = []
    zone_grind_pcts: dict[int, list[float]] = defaultdict(list)
    zone_scores: dict[int, list[float]] = defaultdict(list)
    zone_healing_power: dict[int, list[float]] = defaultdict(list)
    zone_max_healing_power: dict[int, list[float]] = defaultdict(list)
    zone_chain_blockers: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    zone_puzzle_extra_grind: dict[int, list[float]] = defaultdict(list)
    zone_puzzle_chain_sources_agg: dict[int, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    zone_harvest_seconds_agg: dict[int, list[float]] = defaultdict(list)
    zone_harvest_efficiency_agg: dict[int, list[float]] = defaultdict(list)
    zone_item_counts_agg: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for run in all_runs:
        scores.append(run.final_score)
        for zone_id, pct in run.grinding_per_zone.items():
            zone_grind_pcts[zone_id].append(pct)
        for zone_id, s in run.zone_score_map.items():
            zone_scores[zone_id].append(s)
        for zone_id, hp in run.zone_healing_map.items():
            zone_healing_power[zone_id].append(hp)
        for zone_id, hp in run.zone_max_healing_map.items():
            zone_max_healing_power[zone_id].append(hp)
        for zone_id, chain_names in run.zone_blocker_map.items():
            for chain_name in chain_names:
                zone_chain_blockers[zone_id][chain_name] += 1
        for zone_id, extra in run.zone_puzzle_extra_grind.items():
            zone_puzzle_extra_grind[zone_id].append(extra)
        for zone_id, chain_map in run.zone_puzzle_chain_sources.items():
            for chain_key, sources in chain_map.items():
                for source_name, units in sources.items():
                    zone_puzzle_chain_sources_agg[zone_id][chain_key][source_name].append(float(units))
        for zone_id, secs in run.zone_harvest_seconds.items():
            zone_harvest_seconds_agg[zone_id].append(secs)
        for zone_id, eff in run.zone_harvest_efficiency.items():
            zone_harvest_efficiency_agg[zone_id].append(eff)
        for zone_id, snap in run.zone_inventory_snapshot.items():
            for prefab, count in snap.items():
                zone_item_counts_agg[zone_id][prefab].append(count)

    arr = np.array(scores)
    percentiles = {
        f"p{p}": float(np.percentile(arr, p))
        for p in [5, 10, 25, 50, 75, 90, 95]
    }

    return FullRunResult(
        scores=scores,
        score_percentiles=percentiles,
        zone_grind_pcts=dict(zone_grind_pcts),
        zone_scores=dict(zone_scores),
        zone_healing_power=dict(zone_healing_power),
        zone_max_healing_power=dict(zone_max_healing_power),
        zone_chain_blockers={k: dict(v) for k, v in zone_chain_blockers.items()},
        zone_puzzle_extra_grind=dict(zone_puzzle_extra_grind),
        zone_puzzle_chain_sources={
            z: {ck: dict(src) for ck, src in cm.items()}
            for z, cm in zone_puzzle_chain_sources_agg.items()
        },
        zone_harvest_seconds=dict(zone_harvest_seconds_agg),
        zone_harvest_efficiency=dict(zone_harvest_efficiency_agg),
        zone_item_counts={z: dict(d) for z, d in zone_item_counts_agg.items()},
    )
