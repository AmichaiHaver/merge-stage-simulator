from data import LootEntry, LootTable, Plant, MergeChain, ZoneData, ZoneUnlock, GameData
from data import load_data

EXCEL = "data/Discovery Event Layout Generator.xlsx"


def test_parse_zones_count():
    data = load_data(EXCEL)
    # Balance sheet contains all zones (1-25); at minimum zones 1-9 must be present
    assert {1, 2, 3, 4, 5, 6, 7, 8, 9}.issubset(data.zones.keys())


def test_zone_types():
    data = load_data(EXCEL)
    assert data.zones[1].zone_type == "Start"
    assert data.zones[3].zone_type == "Grindy"
    assert data.zones[2].zone_type == "Puzzle"


def test_zone_tile_counts():
    data = load_data(EXCEL)
    assert data.zones[3].tile_count == 60
    assert data.zones[8].tile_count == 186


def test_grindy_zone3_has_brambles():
    data = load_data(EXCEL)
    comp = data.zones[3].composition
    assert "SmallBrambles" in comp
    assert comp["SmallBrambles"] == 10.0


def test_grindy_zone3_has_harvest_away():
    data = load_data(EXCEL)
    assert "Event_LakeCottage_HarvestAway_1" in data.zones[3].composition


def test_loot_entry_fields():
    e = LootEntry(item="Competition_flower_2", probability=0.1)
    assert e.item == "Competition_flower_2"
    assert e.probability == 0.1


def test_merge_chain_index():
    chain = MergeChain(
        name="Puzzle Chain 1",
        items=["Competition_ancient_object_1", "Competition_ancient_object_2"],
    )
    assert chain.items[0] == "Competition_ancient_object_1"
    assert chain.items.index("Competition_ancient_object_2") == 1


def test_zone_data_composition():
    z = ZoneData(
        zone_id=3,
        zone_type="Grindy",
        tile_count=60,
        composition={"SmallBrambles": 10.0, "Brambles": 10.0},
    )
    assert z.tile_count == 60
    assert z.composition["SmallBrambles"] == 10.0


def test_bramble_plants_parsed():
    data = load_data(EXCEL)
    assert "SmallBrambles" in data.plants
    assert "Brambles" in data.plants
    assert "Large_Brambles" in data.plants
    assert "Curly_Small_Brambles" in data.plants


def test_plant_harvest_counts():
    data = load_data(EXCEL)
    assert data.plants["SmallBrambles"].max_harvests == 20
    assert data.plants["Brambles"].max_harvests == 15
    assert data.plants["Large_Brambles"].max_harvests == 30
    assert data.plants["Curly_Small_Brambles"].max_harvests == 15
    assert data.plants["Curly_Brambles"].max_harvests == 10
    assert data.plants["Curly_Large_Brambles"].max_harvests == 20


def test_plant_has_loot_table():
    data = load_data(EXCEL)
    plant = data.plants["SmallBrambles"]
    assert plant.loot_table_name != ""
    assert plant.loot_table_name in data.loot_tables


def test_loot_table_entries():
    data = load_data(EXCEL)
    lt = data.loot_tables["Loot_HarvestSmallBrambles_GERevamp"]
    item_names = [e.item for e in lt.entries]
    assert "Competition_flower_2" in item_names
    assert "Competition_ancient_object_1" in item_names
    assert "Event_LakeCottage_Point_1" in item_names


def test_loot_probabilities_sum_to_one():
    data = load_data(EXCEL)
    lt = data.loot_tables["Loot_HarvestSmallBrambles_GERevamp"]
    total = lt.default_probability + sum(e.probability for e in lt.entries)
    assert abs(total - 1.0) < 0.01


def test_default_item_probability():
    data = load_data(EXCEL)
    lt = data.loot_tables["Loot_HarvestSmallBrambles_GERevamp"]
    assert abs(lt.default_probability - 0.2) < 0.001


def test_puzzle_chains_count():
    data = load_data(EXCEL)
    assert len(data.chains) == 4


def test_ancient_object_chain():
    data = load_data(EXCEL)
    chain = next(c for c in data.chains if "ancient_object_1" in c.items[0])
    assert chain.items[0] == "Competition_ancient_object_1"
    assert chain.items[9] == "Competition_ancient_object_10"
    assert len(chain.items) == 10


def test_currency_chain():
    data = load_data(EXCEL)
    assert data.currency_chain.items[0] == "Event_LakeCottage_Currency_1"
    assert data.currency_chain.items[9] == "Event_LakeCottage_Currency_10"
    assert len(data.currency_chain.items) == 10


def test_zone_unlocks_parsed():
    data = load_data(EXCEL)
    # zone N >= 3 requires Discovery level N (zone N = level N rule)
    assert data.zone_unlocks[1].required_currency_level is None
    assert data.zone_unlocks[3].required_currency_level == 3
    assert data.zone_unlocks[4].required_currency_level == 4
    assert data.zone_unlocks[6].required_currency_level == 6
    assert data.zone_unlocks[9].required_currency_level == 9


def test_zones_without_unlock():
    data = load_data(EXCEL)
    assert data.zone_unlocks[1].required_currency_level is None
