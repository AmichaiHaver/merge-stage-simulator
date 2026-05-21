from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import openpyxl

EXCEL_PATH = "data/Discovery Event Layout Generator.xlsx"

HARVEST_AWAY_PREFIX = "Event_LakeCottage_HarvestAway_"
PUZZLE_CHAIN_NAMES = {"Puzzle Chain 1", "Puzzle Chain 2", "Puzzle Chain 3", "Puzzle Chain 4"}


@dataclass
class LootEntry:
    item: str
    probability: float  # from #INPUT column


@dataclass
class LootTable:
    name: str
    default_item: str
    default_probability: float  # from '#' column
    entries: list[LootEntry] = field(default_factory=list)


@dataclass
class Plant:
    prefab: str
    harvest_time: float
    max_harvests: int
    loot_table_name: str


@dataclass
class MergeChain:
    name: str
    items: list[str]  # ordered low to high level (index 0 = base)


@dataclass
class ZoneData:
    zone_id: int
    zone_type: str  # "Start", "Puzzle", "Grindy"
    tile_count: int
    composition: dict[str, float]  # prefab -> % (0–100)


@dataclass
class ZoneUnlock:
    zone_id: int
    required_currency_level: Optional[int]  # None = no unlock required


@dataclass
class GameData:
    zones: dict[int, ZoneData]
    plants: dict[str, Plant]           # prefab -> Plant
    loot_tables: dict[str, LootTable]  # loot table name -> LootTable
    chains: list[MergeChain]           # puzzle chains only
    currency_chain: MergeChain         # discovery / zone-unlock chain
    zone_unlocks: dict[int, ZoneUnlock]


def load_data(excel_path: str = EXCEL_PATH) -> GameData:
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    zones = _parse_zones(wb)
    plants, loot_tables = _parse_plants_and_loot(wb)
    chains, currency_chain = _parse_chains(wb)
    zone_unlocks = _parse_zone_unlocks(wb)
    return GameData(
        zones=zones,
        plants=plants,
        loot_tables=loot_tables,
        chains=chains,
        currency_chain=currency_chain,
        zone_unlocks=zone_unlocks,
    )


def _parse_zones(wb) -> dict[int, ZoneData]:
    ws = wb["Level_Event_LakeCottage_Balance"]
    rows = list(ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True))

    # Row 0: zone types ("Start", "Puzzle", "Grindy", ...)
    # Row 1: zone numbers (1.0 ... 9.0)
    # Row 2: tile counts
    # Rows 3-4: Item Count, Area % (not needed)
    # Rows 5+: alternating Prefab / % rows
    zone_types = rows[0]
    zone_nums = rows[1]
    tile_counts = rows[2]

    zones: dict[int, ZoneData] = {}
    for col in range(1, 10):  # columns 1–9 → zones 1–9
        zone_id = int(zone_nums[col])
        zone_type = str(zone_types[col])
        tile_count = int(tile_counts[col])

        composition: dict[str, float] = {}
        row_idx = 5
        while row_idx + 1 < len(rows):
            prefab_val = rows[row_idx][col] if col < len(rows[row_idx]) else None
            pct_val = rows[row_idx + 1][col] if col < len(rows[row_idx + 1]) else None
            if prefab_val and pct_val:
                composition[str(prefab_val)] = float(pct_val)
            row_idx += 2

        zones[zone_id] = ZoneData(
            zone_id=zone_id,
            zone_type=zone_type,
            tile_count=tile_count,
            composition=composition,
        )

    return zones


# Stub implementations so load_data() doesn't crash yet
def _parse_plants_and_loot(wb):
    return {}, {}


def _parse_chains(wb):
    return [], MergeChain(name="Discovery Chain", items=[])


def _parse_zone_unlocks(wb):
    return {}
