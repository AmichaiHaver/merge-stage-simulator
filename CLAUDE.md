# Merge Stage Simulator — Project Context

## What it is
Monte Carlo simulator for the LakeCottage event in Merge games.
Simulates N players end-to-end through all zones. Shows score percentiles, grind amounts, blockers, and healing power per zone.

## Architecture
- `data.py` — reads 3 Excel files into dataclasses
- `simulation.py` — Monte Carlo engine
- `app.py` — Streamlit UI
- `tests/` — pytest (38 tests)
- `debug_run.py` — single-player trace tool (`python debug_run.py [seed] [puzzle_pct]`)

## Data files (data/)
| File | Role |
|------|------|
| `Discovery Event Layout Generator 001.xlsx` | Zone structure, Item DB, Zone Gates, Generator LootTable |
| `_Data_Loot - Event LakeCottage.xlsx` | Loot tables for generators |
| `_Data_Objects - Event LakeCottage.xlsx` | HarvestAway definitions, point values |

Defaults loaded automatically. User can override via file upload in UI.
HarvestAway max_harvests = **3** (read from Objects file col 108, not hardcoded).

## Zone structure (Layout 001)
| Zone | Type          | Tiles | Unlock     |
|------|---------------|-------|------------|
| 1    | Start         | 23    | —          |
| 2    | Puzzle        | 65    | —          |
| 3    | Grindy        | 78    | —          |
| 4    | Puzzle        | 159   | —          |
| 5    | Grindy        | 120   | Currency_6 |
| 6    | Puzzle        | 76    | Currency_7 |
| 7    | Grindy        | 70    | Currency_7 |
| 8    | Puzzle/Grindy | 100   | Currency_9 |
| 9    | Puzzle        | 135   | Currency_9 |
| 10+  | Fog           | —     | Currency_9 |

## Simulation model

### `simulate_player_run` — flow
Returns `PlayerRunResult` dataclass (not tuple).

1. Outer `while made_progress` loop — skipped locked zones retried
2. Per zone in order:
   - If locked → `continue`
   - If Grindy:
     1. `discovery_only_pct` — binary search for minimum grind to satisfy downstream zone UNLOCK only
     2. `grinding_pct` — binary search for minimum grind to satisfy unlock + puzzle X% completion
     3. `extra_grind = grinding_pct - discovery_only_pct` stored in `zone_puzzle_extra_grind[pz_id]`
     4. `for…else` records chain bottleneck list when 100% insufficient
   - Otherwise (Puzzle/Start/Fog):
     1. Snapshot `pre_puzzle_chain_units` from current inventory
     2. Apply cached harvest (`inv_delta`); compute `bramble_chain_units` from `inv_delta`
     3. `attempt_puzzle_zone` — returns `(opened, chain_opened, chain_total)`
     4. Record per-chain blockers: all chains where `opened[c] < ceil(total[c] × X%)`
     5. Check discovery (Currency unlock for next zone) → add "Currency" blocker if missing
     6. Store `zone_puzzle_chain_sources[zone_id][chain_key] = {bramble, other}`
3. After each zone → compute score + healing

### `zone_blocker_map: dict[int, list[str]]`
- Grindy zones: single-entry list (`["Currency"]` or `["chain_name"]`) when 100% grind insufficient
- Puzzle zones: list of ALL chains below X% individually (can be multiple)

### Puzzle unlock mechanic (`attempt_puzzle_zone`)
- Each Competition_* tile costs **2× same-level items** from inventory (not base-unit conversion)
- Merge mechanic: 2 inventory + 1 locked board = 3 → 1 next-level (refund to inventory)
- Build lower→higher: 5→2 when need ≥2, 3→1 when need 1 (via `_try_build_n_items`)
- Cannot split higher→lower items
- Tiles sorted lowest level first; cascade: refund may enable further unlocks
- `_try_build_n_items(inv, chain, level, n)` — returns new dict or None, never modifies input
- Bottleneck = chain with lowest opened/total ratio

### `_downstream_puzzle_ids` — definition
Returns only the FIRST puzzle zone after a grindy zone.

### `simulate_harvest` — 3 branches
1. `prefab in data.plants` → roll loot table (or direct item if no table)
2. `prefab.startswith("Event_LakeCottage_HarvestAway_")` → fallback currency
3. `prefab in currency_chain or points_chain` → direct pickup

### Puzzle zone tile handling
- HarvestAway tiles → harvested via `simulate_harvest` (branch 1)
- Brambles → harvested via `simulate_harvest` (branch 1)
- Competition_* tiles → puzzle unlock mechanic
- PlainGrass → ignored (no loot table result)
- Points/Currency direct tiles → direct pickup (branch 3)

## FullRunResult fields (simulation.py)
- `scores`, `score_percentiles` (p5/10/25/50/75/90/95)
- `zone_grind_pcts` — grindy zone → list of grinding % per player
- `zone_scores` — zone → cumulative score per player who reached it
- `zone_healing_power` — zone → cumulative Life Orb healing power per player
- `zone_chain_blockers` — **all zone types** → {chain_display_name: count} — covers grindy (100% grind failed) and puzzle (chain below X%)
- `zone_puzzle_extra_grind` — puzzle zone → per-player list of (grinding_pct − discovery_only_pct)
- `zone_puzzle_chain_sources` — puzzle zone → chain_key → {`bramble`: [base_units/player], `other`: [base_units/player]}

## `_compute_chain_base_units(items, chains) → dict[str, int]`
Base units: `count × 3^level` (level = index in chain.items). chain_key = chain.items[0].
Level 1 item = 3 units, level 2 = 9 units. Used for source breakdown tracking.

## Life Orbs
- Prefab pattern: `Life_Orb_N_Root` (N=1–6 seen in current event loot)
- Healing power map: {0:1, 1:4, 2:16, 3:64, 4:256, 5:1024, 6:4096, 7:16384, 8:65536, 9:327680}
- `_compute_healing_power(inventory)` in simulation.py

## UI panels (app.py)
1. **Score Percentiles per Zone** — table p5/10/25/50/75/90/95 × zone
2. **Grinding Needed per Grindy Zone** — box plots
3. **Chain Blockers per Zone** — all zones (G/P), all chains below X% individually + Currency
4. **Extra Grind for Puzzle Zones** — box plots of (grinding_pct − discovery_only_pct) per puzzle zone
5. **Chain Item Sources per Puzzle Zone** — table: median base units from bramble vs other, per chain
6. **Healing Power Percentiles per Zone** — table p5–p95 × zone, Life Orbs
- Sidebar: Players (500–10k), Required Puzzle Completion %

## Performance note
`_try_build_n_items` (recursive merge builder) is the bottleneck at high completion %.
At 50%: ~0.056s/player. At 80%: ~0.33s/player. Use ≤50% or ≤500 players for fast runs.

## Constants
- `CURRENCY_MERGE_RATIO = 2.5`
- `HARVEST_AWAY_PREFIX = "Event_LakeCottage_HarvestAway_"`
- `LIFE_ORB_HEALING_POWER` — level→power dict (simulation.py)
- Grinding increments: 5% (20 steps max)
- Default players: 2,000

## Run
```bash
cd "/Users/amichai.haver/Downloads/Claude small projects/Simulation builder"
source .venv/bin/activate
streamlit run app.py          # http://localhost:8501
pytest tests/ -v
```
