from __future__ import annotations
import math
from collections import defaultdict
from typing import Optional

import numpy as np

from data import GameData, ZoneData, MergeChain, ZoneUnlock

HARVEST_AWAY_PREFIX = "Event_LakeCottage_HarvestAway_"


def simulate_harvest(
    grindy_zone: ZoneData,
    grinding_pct: float,             # 0.0–1.0
    currency_per_harvest_away: int,  # Currency_1 per HarvestAway harvest
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
            currency_key = "Event_LakeCottage_Currency_1"
            inventory[currency_key] += (
                n_items * harvest_away_max_harvests * currency_per_harvest_away
            )

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
