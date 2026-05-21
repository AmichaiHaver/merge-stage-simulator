from __future__ import annotations
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from data import GameData, ZoneData, MergeChain, ZoneUnlock


@dataclass
class BottleneckReport:
    grinding_pct: float
    unlock_fail_rate: float
    puzzle_fail_rate: float
    missing_item_rates: dict[str, float] = field(default_factory=dict)

HARVEST_AWAY_PREFIX = "Event_LakeCottage_HarvestAway_"
CURRENCY_MERGE_RATIO = 2.5  # 5 currency items → 2 at next level


def _harvest_away_base_currency_level(zone_id: int) -> int:
    """Zones 1-3 → base 1, zones 4-6 → base 2, zones 7-9 → base 3."""
    if zone_id <= 3:
        return 1
    elif zone_id <= 6:
        return 2
    return 3


def simulate_harvest(
    grindy_zone: ZoneData,
    grinding_pct: float,             # 0.0–1.0
    harvest_away_max_harvests: int,  # max harvests per HarvestAway item
    data: GameData,
    rng: np.random.Generator,
) -> dict[str, int]:
    inventory: dict[str, int] = defaultdict(int)

    for prefab, zone_pct in grindy_zone.composition.items():
        n_items = round((zone_pct / 100) * grindy_zone.tile_count * grinding_pct)
        if n_items == 0:
            continue

        if prefab.startswith(HARVEST_AWAY_PREFIX):
            base = _harvest_away_base_currency_level(grindy_zone.zone_id)
            levels = [base, base + 1, base + 2]
            currency_items = [
                data.currency_chain.items[lvl - 1]
                for lvl in levels
                if lvl - 1 < len(data.currency_chain.items)
            ]
            total_harvests = n_items * harvest_away_max_harvests
            chosen = rng.integers(0, len(currency_items), size=total_harvests)
            for idx in chosen:
                inventory[currency_items[idx]] += 1

        elif prefab in data.plants:
            plant = data.plants[prefab]
            lt = data.loot_tables.get(plant.loot_table_name)
            if not lt:
                continue

            names = [lt.default_item] + [e.item for e in lt.entries]
            probs = [lt.default_probability] + [e.probability for e in lt.entries]
            total = sum(probs)
            if total == 0:
                continue
            probs_arr = np.array(probs, dtype=float) / total

            total_rolls = n_items * plant.max_harvests
            chosen = rng.choice(len(names), size=total_rolls, p=probs_arr)
            for idx in chosen:
                inventory[names[idx]] += 1

    return dict(inventory)


def check_puzzle_completion(
    inventory: dict[str, int],
    puzzle_zone: ZoneData,
    required_merge_pct: float,
    chains: list[MergeChain],
) -> bool:
    inv = dict(inventory)

    puzzle_items: list[tuple[int, MergeChain]] = []
    for prefab, zone_pct in puzzle_zone.composition.items():
        count = max(1, round((zone_pct / 100) * puzzle_zone.tile_count))
        for chain in chains:
            if prefab in chain.items:
                level = chain.items.index(prefab)
                puzzle_items.extend([(level, chain)] * count)
                break

    if not puzzle_items:
        return True

    puzzle_items.sort(key=lambda x: x[0])
    required_count = math.ceil(len(puzzle_items) * required_merge_pct)

    for level, chain in puzzle_items[:required_count]:
        cost_base = 2 * (3 ** max(0, level - 1))
        available_base = sum(
            inv.get(chain.items[l], 0) * (3 ** l)
            for l in range(min(level + 1, len(chain.items)))
        )
        if available_base < cost_base:
            return False

        remaining = cost_base
        for l in range(level, -1, -1):
            item = chain.items[l]
            unit_val = 3 ** l
            can_take = min(inv.get(item, 0), remaining // unit_val)
            inv[item] = inv.get(item, 0) - can_take
            remaining -= can_take * unit_val
            if remaining == 0:
                break

    return True


def check_zone_unlock(
    inventory: dict[str, int],
    zone_unlock: ZoneUnlock,
    currency_chain: MergeChain,
) -> bool:
    if zone_unlock.required_currency_level is None:
        return True

    k = zone_unlock.required_currency_level
    needed_base = CURRENCY_MERGE_RATIO ** (k - 1)

    available = sum(
        inventory.get(item, 0) * (CURRENCY_MERGE_RATIO ** i)
        for i, item in enumerate(currency_chain.items)
    )
    return available >= needed_base


def _add_inventories(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    result = dict(a)
    for k, v in b.items():
        result[k] = result.get(k, 0) + v
    return result


def build_curve(
    grindy_zone_id: int,
    puzzle_zone_id: int,
    required_merge_pct: float,
    harvest_away_max_harvests: int,
    n_simulations: int,
    data: GameData,
) -> dict[int, float]:
    grindy_zone = data.zones[grindy_zone_id]
    puzzle_zone = data.zones[puzzle_zone_id]
    unlock = data.zone_unlocks.get(puzzle_zone_id, ZoneUnlock(puzzle_zone_id, None))

    # All zones before the current grindy zone contribute inventory at 100%
    prior_zone_ids = list(range(1, grindy_zone_id))

    rng = np.random.default_rng()
    curve: dict[int, float] = {}

    for pct_int in range(0, 105, 5):
        grinding_pct = pct_int / 100
        successes = 0
        for _ in range(n_simulations):
            # Accumulate inventory from all prior zones at 100%
            inv: dict[str, int] = {}
            for prior_id in prior_zone_ids:
                prior_inv = simulate_harvest(
                    data.zones[prior_id], 1.0,
                    harvest_away_max_harvests,
                    data, rng,
                )
                inv = _add_inventories(inv, prior_inv)
            # Current grindy zone at variable %
            inv = _add_inventories(
                inv,
                simulate_harvest(
                    grindy_zone, grinding_pct,
                    harvest_away_max_harvests,
                    data, rng,
                ),
            )
            ok = check_puzzle_completion(inv, puzzle_zone, required_merge_pct, data.chains)
            ok = ok and check_zone_unlock(inv, unlock, data.currency_chain)
            if ok:
                successes += 1
        curve[pct_int] = successes / n_simulations

    return curve


def _check_puzzle_with_missing(
    inventory: dict[str, int],
    puzzle_zone: ZoneData,
    required_merge_pct: float,
    chains: list[MergeChain],
) -> tuple[bool, list[str]]:
    inv = dict(inventory)
    puzzle_items: list[tuple[int, MergeChain, str]] = []
    for prefab, zone_pct in puzzle_zone.composition.items():
        count = max(1, round((zone_pct / 100) * puzzle_zone.tile_count))
        for chain in chains:
            if prefab in chain.items:
                level = chain.items.index(prefab)
                puzzle_items.extend([(level, chain, prefab)] * count)
                break

    if not puzzle_items:
        return True, []

    puzzle_items.sort(key=lambda x: x[0])
    required_count = math.ceil(len(puzzle_items) * required_merge_pct)
    missing: list[str] = []

    for level, chain, prefab in puzzle_items[:required_count]:
        cost_base = 2 * (3 ** max(0, level - 1))
        available_base = sum(
            inv.get(chain.items[l], 0) * (3 ** l)
            for l in range(min(level + 1, len(chain.items)))
        )
        if available_base < cost_base:
            missing.append(prefab)
            continue

        remaining = cost_base
        for l in range(level, -1, -1):
            item = chain.items[l]
            unit_val = 3 ** l
            can_take = min(inv.get(item, 0), remaining // unit_val)
            inv[item] = inv.get(item, 0) - can_take
            remaining -= can_take * unit_val
            if remaining == 0:
                break

    return len(missing) == 0, missing


def build_bottleneck_report(
    grindy_zone_id: int,
    puzzle_zone_id: int,
    required_merge_pct: float,
    harvest_away_max_harvests: int,
    n_simulations: int,
    grinding_pct: float,
    data: GameData,
) -> BottleneckReport:
    grindy_zone = data.zones[grindy_zone_id]
    puzzle_zone = data.zones[puzzle_zone_id]
    unlock = data.zone_unlocks.get(puzzle_zone_id, ZoneUnlock(puzzle_zone_id, None))
    prior_zone_ids = list(range(1, grindy_zone_id))

    rng = np.random.default_rng()
    unlock_failures = 0
    puzzle_failures = 0
    missing_counts: dict[str, int] = defaultdict(int)

    for _ in range(n_simulations):
        inv: dict[str, int] = {}
        for prior_id in prior_zone_ids:
            inv = _add_inventories(
                inv,
                simulate_harvest(data.zones[prior_id], 1.0, harvest_away_max_harvests, data, rng),
            )
        inv = _add_inventories(
            inv,
            simulate_harvest(grindy_zone, grinding_pct, harvest_away_max_harvests, data, rng),
        )

        if not check_zone_unlock(inv, unlock, data.currency_chain):
            unlock_failures += 1

        ok, missing = _check_puzzle_with_missing(inv, puzzle_zone, required_merge_pct, data.chains)
        if not ok:
            puzzle_failures += 1
            for item in missing:
                missing_counts[item] += 1

    return BottleneckReport(
        grinding_pct=grinding_pct,
        unlock_fail_rate=unlock_failures / n_simulations,
        puzzle_fail_rate=puzzle_failures / n_simulations,
        missing_item_rates={k: v / n_simulations for k, v in missing_counts.items()},
    )
