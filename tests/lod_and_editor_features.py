import math
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from campaignforge.assets import AssetLibrary
from campaignforge.context import context_for_entity, resolve_selection, road_entrances_for_entity
from campaignforge.generators import (
    CastleGenerator,
    CaveGenerator,
    ContextRegionGenerator,
    DetailSettings,
    MageTowerGenerator,
    TownGenerator,
)
from campaignforge.model import CampaignState, Entity, GenerationConfig, Scene
from campaignforge.persistence import load_campaign, save_campaign


def exhaust(generator):
    for _ in generator.generate():
        pass


def test_multi_city_lod():
    image = Image.new("RGB", (1000, 600), (146, 173, 105))
    city1 = Entity("city1", "settlement", "town", "Northwatch", 280, 260, 58, 58, metadata={"biome": "plains", "world_ref": "north"})
    city2 = Entity("city2", "settlement", "village", "Brookmere", 720, 360, 42, 42, metadata={"biome": "plains", "world_ref": "brook"})
    parent = Scene(
        "world",
        "World",
        "world",
        image,
        1,
        biome="plains",
        semantic="world",
        entities=[city1, city2],
        paths=[{"kind": "road", "from_ref": "north", "to_ref": "brook", "major": True, "points": [[280,260],[430,300],[570,330],[720,360]]}],
        metadata={"biome_grid": [["plains"] * 20 for _ in range(12)]},
    )
    rect = (180, 160, 820, 480)
    context = resolve_selection(parent, rect)
    assert context.semantic == "region", context
    assert len(context.focus_entities) == 2
    assert context.lod <= 1

    status = lambda *_: None
    settings = DetailSettings(1200, 600, 22, 6, 8, 4, lod=context.lod, selection_fraction=context.selection_fraction)
    gen = ContextRegionGenerator(parent, rect, settings, GenerationConfig(), AssetLibrary(), status)
    exhaust(gen)
    inherited = {e.metadata.get("origin_entity_id"): e for e in gen.entities if e.metadata.get("origin_entity_id")}
    assert "city1" in inherited and "city2" in inherited
    assert inherited["city1"].subtype == "town"
    assert inherited["city2"].subtype == "village"
    scale = 1200 / (rect[2] - rect[0])
    source_distance = math.dist((city1.x, city1.y), (city2.x, city2.y))
    child_distance = math.dist((inherited["city1"].x, inherited["city1"].y), (inherited["city2"].x, inherited["city2"].y))
    assert abs(child_distance - source_distance * scale) < 2.0



def test_crossing_road_is_inherited_when_endpoints_are_outside():
    image = Image.new("RGB", (1000, 600), (146, 173, 105))
    parent = Scene(
        "world", "World", "world", image, 1, biome="plains", semantic="world",
        paths=[{"id": "cross", "kind": "road", "major": True, "points": [[0, 300], [1000, 300]]}],
        metadata={"biome_grid": [["plains"] * 20 for _ in range(12)]},
    )
    rect = (300, 150, 700, 450)
    gen = ContextRegionGenerator(parent, rect, DetailSettings(800, 600, 7, 5, 2, 1, lod=1), GenerationConfig(), AssetLibrary(), lambda *_: None)
    exhaust(gen)
    inherited = [p for p in gen.paths if p.get("id") == "cross" and p.get("inherited")]
    assert inherited, gen.paths
    points = inherited[0]["points"]
    assert points[0][0] <= 1 and points[-1][0] >= 799

def test_road_continuity():
    image = Image.new("RGB", (900, 600), (146, 173, 105))
    town = Entity("town", "settlement", "town", "Waycross", 450, 300, 58, 58, metadata={"biome": "plains", "world_ref": "waycross"})
    paths = [
        {"kind": "road", "from_ref": "waycross", "to_ref": "west", "major": True, "points": [[450,300],[360,300],[250,300],[80,300]]},
        {"kind": "road", "from_ref": "waycross", "to_ref": "north", "major": True, "points": [[450,300],[450,220],[450,130],[450,20]]},
    ]
    parent = Scene("region", "Region", "region", image, 1, biome="plains", semantic="region", entities=[town], paths=paths, metadata={"biome_grid": [["plains"]*20 for _ in range(12)]})
    entrances = road_entrances_for_entity(parent, town)
    assert len(entrances) == 2, entrances
    gen = TownGenerator(town, DetailSettings(1100, 800, 9, 7, 24, 5, lod=3), GenerationConfig(), AssetLibrary(), lambda *_: None, entrances)
    exhaust(gen)
    major = [p for p in gen.paths if p.get("kind") == "road" and p.get("major")]
    assert len(major) == 2
    assert gen.metadata.get("major_road_count") == 2


def test_major_location_types():
    status = lambda *_: None
    config = GenerationConfig()
    assets = AssetLibrary()
    for subtype, expected, cls in [
        ("castle", "castle", CastleGenerator),
        ("wizard_tower", "mage_tower", MageTowerGenerator),
        ("cave", "cave", CaveGenerator),
    ]:
        entity = Entity(subtype, "landmark" if subtype != "castle" else "settlement", subtype, subtype.replace("_", " ").title(), 200, 200, 50, 50, metadata={"biome": "hills", "world_ref": subtype})
        scene = Scene("s", "S", "region", Image.new("RGB", (500,400)), 1, biome="hills", entities=[entity])
        context = context_for_entity(scene, entity)
        assert context.semantic == expected
        gen = cls(entity, DetailSettings(900, 700, 33, 6, 10, 4, lod=3), config, assets, status, [0.0])
        exhaust(gen)
        assert gen.semantic == expected


def test_safe_scene_deletion():
    image = Image.new("RGB", (100, 100))
    state = CampaignState()
    root = Scene("root", "World", "world", image.copy(), 1)
    a = Scene("a", "Area A", "region", image.copy(), 2, parent_id="root", source_rect=(0,0,50,50))
    b = Scene("b", "Area B", "region", image.copy(), 3, parent_id="root", source_rect=(50,0,100,50))
    child = Scene("child", "Town", "town", image.copy(), 4, parent_id="a", source_rect=(10,10,30,30))
    root.entities.append(Entity("portal", "settlement", "town", "Town", 20, 20, child_scene_id="child"))
    for scene in (root, a, b, child):
        state.add_scene(scene)
    removed = state.remove_scene("a", include_descendants=True)
    assert set(removed) == {"a", "child"}
    assert "root" in state.scenes and "b" in state.scenes
    assert state.scenes["root"].entities[0].child_scene_id is None
    assert state.remove_scene("root") == []


def test_asset_editing_is_reversible():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "token.png"
        img = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
        for x in range(16, 32):
            for y in range(32):
                img.putpixel((x, y), (0, 0, 255, 255))
        img.save(path)
        assets = AssetLibrary()
        asset = assets.import_png(str(path))
        original = assets.get(asset.id)
        assert original is not None
        assert assets.rename(asset.id, "Hero Token")
        assert assets.assets[asset.id].name == "Hero Token"
        assets.set_pixelation(asset.id, 8)
        pixelated = assets.get(asset.id)
        assert pixelated is not None and pixelated.size == original.size
        assets.reset_pixelation(asset.id)
        restored = assets.get(asset.id)
        assert restored is not None
        assert restored.tobytes() == original.tobytes()



def test_asset_and_transform_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        png = tmp_path / "hero.png"
        Image.new("RGBA", (24, 36), (80, 160, 220, 255)).save(png)
        assets = AssetLibrary()
        asset = assets.import_png(str(png))
        assets.rename(asset.id, "Scout")
        assets.set_pixelation(asset.id, 5)
        entity = Entity(
            "hero", "custom", "png", "Scout", 40, 50, 64, 96, movable=True, asset_id=asset.id,
            metadata={"rotation": 30, "flip_h": True, "flip_v": False, "keep_aspect": True},
        )
        state = CampaignState()
        state.add_scene(Scene("root", "World", "world", Image.new("RGB", (120, 90)), 1, entities=[entity]))
        target = tmp_path / "test.cforge"
        save_campaign(str(target), state, assets)
        loaded, loaded_assets, extraction = load_campaign(str(target))
        loaded_asset = loaded_assets.assets[asset.id]
        loaded_entity = loaded.scenes["root"].entities[0]
        assert loaded_asset.name == "Scout" and loaded_asset.pixelation == 5
        assert loaded_entity.metadata["rotation"] == 30 and loaded_entity.metadata["flip_h"] is True
        assert loaded_entity.width == 64 and loaded_entity.height == 96
        shutil.rmtree(extraction, ignore_errors=True)

def run():
    test_multi_city_lod()
    test_crossing_road_is_inherited_when_endpoints_are_outside()
    test_road_continuity()
    test_major_location_types()
    test_safe_scene_deletion()
    test_asset_editing_is_reversible()
    test_asset_and_transform_persistence()


if __name__ == "__main__":
    run()
    print("LOD/editor feature tests passed")
