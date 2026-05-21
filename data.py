from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

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
