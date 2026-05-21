from data import LootEntry, LootTable, Plant, MergeChain, ZoneData, ZoneUnlock, GameData


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
