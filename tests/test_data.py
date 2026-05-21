from data import LootEntry, LootTable, Plant, MergeChain, ZoneData, ZoneUnlock, GameData
from data import load_data

EXCEL = "data/Discovery Event Layout Generator.xlsx"


def test_parse_zones_count():
    data = load_data(EXCEL)
    assert set(data.zones.keys()) == {1, 2, 3, 4, 5, 6, 7, 8, 9}


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
