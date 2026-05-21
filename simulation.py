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
