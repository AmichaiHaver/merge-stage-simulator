from __future__ import annotations
import math
from collections import defaultdict
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
            plant = data.plants[prefab]
            lt = data.loot_tables.get(plant.loot_table_name)
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
                # loot_table_name is a direct item prefab (e.g. HarvestAway drops specific currency)
                inventory[plant.loot_table_name] += total_rolls

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

        elif (prefab in data.currency_chain.items
              or prefab in data.points_chain.items):
            # Currency / point items placed directly on the board — pick up as-is
            inventory[prefab] += n_items

    return dict(inventory)


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
      - Spend 2× level-L items from inventory (build from lower if needed)
      - Gain 1× level-(L+1) item (merge result of 2 inventory + 1 locked board tile)

    Cascades: a refund from one unlock may enable unlocking another tile.
    Tiles sorted lowest level first (cheapest unlocks first, cascades upward).

    Modifies inv in-place with net changes (items spent and refunds received).
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
    current = dict(inv)

    made_progress = True
    while made_progress:
        made_progress = False
        next_remaining: list[tuple[int, MergeChain, str]] = []
        for level, chain, chain_key in remaining:
            result = _try_build_n_items(current, chain, level, 2)
            if result is not None:
                # Spend 2× level-L (unlock cost + board merge partner)
                result[chain.items[level]] = result.get(chain.items[level], 0) - 2
                # Refund 1× level-(L+1) (merge result: 2 inventory + 1 locked = 3 → 1 next)
                if level + 1 < len(chain.items):
                    next_item = chain.items[level + 1]
                    result[next_item] = result.get(next_item, 0) + 1
                current = result
                chain_opened[chain_key] = chain_opened.get(chain_key, 0) + 1
                opened_count += 1
                made_progress = True
            else:
                next_remaining.append((level, chain, chain_key))
        remaining = next_remaining

    # Commit net inventory changes back to caller's dict
    inv.clear()
    inv.update(current)
    return opened_count, chain_opened, chain_total


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
    zone_healing_power: dict[int, list[float]]    # zone_id → cumulative healing power per player
    zone_chain_blockers: dict[int, dict[str, int]]
    # grindy zone → {chain_display_name: player_count}
    # 'Currency' means zone unlock was the blocker; chain name means puzzle chain was


@dataclass
class PlayerRunResult:
    final_score: float
    grinding_per_zone: dict[int, float]
    completed_zones: set[int]
    zone_score_map: dict[int, float]
    zone_healing_map: dict[int, float]
    zone_blocker_map: dict[int, list[str]]       # zone_id → list of chain display names
    zone_puzzle_extra_grind: dict[int, float]    # puzzle_zone_id → (grinding_pct - discovery_only_pct)
    zone_puzzle_chain_sources: dict[int, dict[str, dict[str, int]]]
    # puzzle_zone_id → chain_key → {'bramble': base_units, 'other': base_units}


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
    grinding_per_zone: dict[int, float] = {}
    completed_zones: set[int] = set()
    zone_score_map: dict[int, float] = {}
    zone_healing_map: dict[int, float] = {}
    zone_blocker_map: dict[int, list[str]] = {}
    zone_puzzle_extra_grind: dict[int, float] = {}
    zone_puzzle_chain_sources: dict[int, dict[str, dict[str, int]]] = {}
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

            if zone.zone_type == "Grindy":
                downstream = _downstream_puzzle_ids(zone_id, data.zones)

                # Pre-simulate full grindy harvest once; scale for stepping
                full_grindy_inv = simulate_harvest(zone, 1.0, harvest_away_max_harvests, data, rng)

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
                        pz_copy = dict(test_inv)
                        opened, _, chain_total = attempt_puzzle_zone(pz_copy, pz, data.chains)
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
                            pz_copy = dict(final_test)
                            opened, chain_opened, chain_total = attempt_puzzle_zone(
                                pz_copy, pz, data.chains
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
                grinding_per_zone[zone_id] = grinding_pct

            else:
                # Non-grindy zone (Start / Puzzle / Fog):
                # Apply pre-simulated harvest if available, otherwise simulate now
                if zone_id in cached_puzzle_harvests:
                    inv_delta = cached_puzzle_harvests.pop(zone_id)
                else:
                    inv_delta = simulate_harvest(zone, 1.0, harvest_away_max_harvests, data, rng)
                inventory = _add_inventories(inventory, inv_delta)

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

            completed_zones.add(zone_id)
            made_progress = True
            zone_score_map[zone_id] = _compute_score(inventory, data.point_values, data.points_chain)
            zone_healing_map[zone_id] = _compute_healing_power(inventory)

    final_score = _compute_score(inventory, data.point_values, data.points_chain)
    return PlayerRunResult(
        final_score=final_score,
        grinding_per_zone=grinding_per_zone,
        completed_zones=completed_zones,
        zone_score_map=zone_score_map,
        zone_healing_map=zone_healing_map,
        zone_blocker_map=zone_blocker_map,
        zone_puzzle_extra_grind=zone_puzzle_extra_grind,
        zone_puzzle_chain_sources=zone_puzzle_chain_sources,
    )


def build_full_run_results(
    data: GameData,
    puzzle_completion_pct: float,
    harvest_away_max_harvests: int,
    n_players: int,
) -> FullRunResult:
    rng = np.random.default_rng()
    scores: list[float] = []
    zone_grind_pcts: dict[int, list[float]] = defaultdict(list)
    zone_scores: dict[int, list[float]] = defaultdict(list)
    zone_healing_power: dict[int, list[float]] = defaultdict(list)
    zone_chain_blockers: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _ in range(n_players):
        run = simulate_player_run(data, puzzle_completion_pct, harvest_away_max_harvests, rng)
        scores.append(run.final_score)
        for zone_id, pct in run.grinding_per_zone.items():
            zone_grind_pcts[zone_id].append(pct)
        for zone_id, s in run.zone_score_map.items():
            zone_scores[zone_id].append(s)
        for zone_id, hp in run.zone_healing_map.items():
            zone_healing_power[zone_id].append(hp)
        for zone_id, chain_names in run.zone_blocker_map.items():
            for chain_name in chain_names:
                zone_chain_blockers[zone_id][chain_name] += 1

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
        zone_chain_blockers={k: dict(v) for k, v in zone_chain_blockers.items()},
    )
