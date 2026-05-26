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
    points_chain: MergeChain           # Event_LakeCottage_Point_1..N ordered low→high
    point_values: dict[str, int]       # prefab -> score value


def load_data(excel_path: str = EXCEL_PATH) -> GameData:
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    zones = _parse_zones(wb)
    plants, loot_tables = _parse_plants_and_loot(wb)
    chains, currency_chain = _parse_chains(wb)
    zone_unlocks = _parse_zone_unlocks(wb)
    points_chain, point_values = _parse_points_chain(wb)
    return GameData(
        zones=zones,
        plants=plants,
        loot_tables=loot_tables,
        chains=chains,
        currency_chain=currency_chain,
        zone_unlocks=zone_unlocks,
        points_chain=points_chain,
        point_values=point_values,
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


def _parse_plants_and_loot(wb) -> tuple[dict[str, Plant], dict[str, LootTable]]:
    item_ws = wb["Item Database"]
    plants: dict[str, Plant] = {}

    for row in item_ws.iter_rows(min_row=2, max_row=item_ws.max_row, values_only=True):
        prefab = row[3]
        harvestable = row[5]
        harvest_time = row[7]
        harvest_count = row[9]

        if prefab and harvestable == "x" and harvest_time and harvest_count:
            plants[str(prefab)] = Plant(
                prefab=str(prefab),
                harvest_time=float(harvest_time),
                max_harvests=int(harvest_count),
                loot_table_name="",
            )

    loot_ws = wb["Generator LootTable"]
    loot_tables: dict[str, LootTable] = {}

    for row in loot_ws.iter_rows(min_row=2, max_row=loot_ws.max_row, values_only=True):
        if not row[0]:
            continue
        main_prefab = str(row[0])
        lt_name = str(row[1]) if row[1] else main_prefab
        default_item = str(row[2]) if row[2] else ""
        default_prob = float(row[4]) if row[4] else 0.0

        entries: list[LootEntry] = []
        for i in range(7):
            item_col = 5 + i * 4
            input_col = 8 + i * 4   # #INPUT column (NOT Chance column at 7+i*4)
            if item_col < len(row) and input_col < len(row):
                item_val = row[item_col]
                prob_val = row[input_col]
                if item_val and prob_val:
                    entries.append(LootEntry(item=str(item_val), probability=float(prob_val)))

        lt = LootTable(
            name=lt_name,
            default_item=default_item,
            default_probability=default_prob,
            entries=entries,
        )
        loot_tables[lt_name] = lt

        if main_prefab in plants:
            old = plants[main_prefab]
            plants[main_prefab] = Plant(
                prefab=old.prefab,
                harvest_time=old.harvest_time,
                max_harvests=old.max_harvests,
                loot_table_name=lt_name,
            )

    return plants, loot_tables


def _parse_chains(wb) -> tuple[list[MergeChain], MergeChain]:
    item_ws = wb["Item Database"]
    chain_items: dict[str, list[str]] = {}
    currency_items: list[str] = []

    for row in item_ws.iter_rows(min_row=2, max_row=item_ws.max_row, values_only=True):
        chain_name = row[0]
        prefab = row[3]
        mergeable = row[4]

        if not prefab or not chain_name:
            continue

        if str(chain_name) == "Discovery Chain":
            currency_items.append(str(prefab))
        elif mergeable == "x" and str(chain_name) in PUZZLE_CHAIN_NAMES:
            if chain_name not in chain_items:
                chain_items[str(chain_name)] = []
            chain_items[str(chain_name)].append(str(prefab))

    chains = [MergeChain(name=name, items=items) for name, items in chain_items.items()]
    currency_chain = MergeChain(name="Discovery Chain", items=currency_items)
    return chains, currency_chain


def _parse_points_chain(wb) -> tuple[MergeChain, dict[str, int]]:
    item_ws = wb["Item Database"]
    point_items: list[str] = []

    for row in item_ws.iter_rows(min_row=2, max_row=item_ws.max_row, values_only=True):
        chain_name = row[0]
        prefab = row[3]
        if chain_name and "Point" in str(chain_name) and prefab:
            point_items.append(str(prefab))

    # Score value = 3^index: Point_1=1, Point_2=3, Point_3=9, ...
    # Update once actual game score values are confirmed from Excel
    point_values = {prefab: 3 ** i for i, prefab in enumerate(point_items)}
    return MergeChain(name="Points Chain", items=point_items), point_values


def _parse_zone_unlocks(wb) -> dict[int, ZoneUnlock]:
    ws = wb["Zone unlock"]
    unlocks: dict[int, ZoneUnlock] = {}

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        if row[1] is None:
            continue
        try:
            zone_id = int(row[1])
        except (ValueError, TypeError):
            continue
        item = row[2]

        if item:
            level = int(str(item).split("_")[-1])
            unlocks[zone_id] = ZoneUnlock(zone_id=zone_id, required_currency_level=level)
        else:
            unlocks[zone_id] = ZoneUnlock(zone_id=zone_id, required_currency_level=None)

    return unlocks
