import math
import os
import shutil
import statistics
import tempfile
from pathlib import Path

from PIL import Image

from campaignforge.assets import AssetLibrary
from campaignforge.generators import BuildingInteriorGenerator
from campaignforge.grid import snap_point
from campaignforge.local_saves import list_local_saves, save_path
from campaignforge.model import CampaignState, Entity, GenerationConfig, Scene
from campaignforge.persistence import load_campaign, save_campaign
from campaignforge.world_engine import FantasyMapGenerator, MapSettings


def exhaust(generator):
    for _ in generator.generate():
        pass


def terrain_counts(engine: FantasyMapGenerator) -> dict[tuple[int, int, int], int]:
    counts: dict[tuple[int, int, int], int] = {}
    for row in range(engine.rows):
        for col in range(engine.cols):
            color = engine._terrain_color(
                engine.elevation[row][col],
                engine.moisture[row][col],
                engine.temperature[row][col],
            )
            counts[color] = counts.get(color, 0) + 1
    return counts


def test_temperature_and_altitude_change_world_character():
    status = lambda *_: None
    hot = FantasyMapGenerator(
        MapSettings(760, 500, 81173, 5, 4, 7, temperature=92, max_altitude=18), status
    )
    cold = FantasyMapGenerator(
        MapSettings(760, 500, 81173, 5, 4, 7, temperature=18, max_altitude=96), status
    )
    exhaust(hot)
    exhaust(cold)

    hot_counts = terrain_counts(hot)
    cold_counts = terrain_counts(cold)
    hot_dry = hot_counts.get(hot.DESERT, 0) + hot_counts.get(hot.DRY_PLAINS, 0)
    cold_dry = cold_counts.get(cold.DESERT, 0) + cold_counts.get(cold.DRY_PLAINS, 0)
    assert hot_dry > cold_dry
    assert cold_counts.get(cold.SNOW, 0) > hot_counts.get(hot.SNOW, 0)

    hot_elev = [value for row in hot.elevation for value in row]
    cold_elev = [value for row in cold.elevation for value in row]
    assert statistics.pstdev(cold_elev) > statistics.pstdev(hot_elev)
    assert cold.mountain_level < hot.mountain_level


def test_roads_respect_terrain_and_degree_limits():
    status = lambda *_: None
    engine = FantasyMapGenerator(
        MapSettings(
            900, 620, 44991, 6, 4, 12,
            temperature=50,
            max_altitude=82,
            road_min_per_settlement=1,
            road_max_per_settlement=2,
            road_connection_chance=80,
        ),
        status,
    )
    exhaust(engine)
    assert engine.road_paths

    degree = [0 for _ in engine.settlements]
    for path in engine.road_paths:
        assert len(path) >= 2
        for x, y in path:
            assert engine._is_land(x, y), (x, y)
        start = min(range(len(engine.settlements)), key=lambda i: math.dist(path[0], (engine.settlements[i].x, engine.settlements[i].y)))
        end = min(range(len(engine.settlements)), key=lambda i: math.dist(path[-1], (engine.settlements[i].x, engine.settlements[i].y)))
        assert start != end
        degree[start] += 1
        degree[end] += 1
    assert max(degree, default=0) <= 2

    isolated = FantasyMapGenerator(
        MapSettings(
            760, 500, 44991, 5, 2, 8,
            road_min_per_settlement=0,
            road_max_per_settlement=3,
            road_connection_chance=0,
        ),
        status,
    )
    exhaust(isolated)
    isolated_degree = [0 for _ in isolated.settlements]
    for path in isolated.road_paths:
        start = min(range(len(isolated.settlements)), key=lambda i: math.dist(path[0], (isolated.settlements[i].x, isolated.settlements[i].y)))
        end = min(range(len(isolated.settlements)), key=lambda i: math.dist(path[-1], (isolated.settlements[i].x, isolated.settlements[i].y)))
        isolated_degree[start] += 1
        isolated_degree[end] += 1
    assert any(value == 0 for value in isolated_degree)
    assert len(isolated.road_paths) < max(1, len(isolated.settlements) // 2)


def test_rivers_follow_elevation():
    engine = FantasyMapGenerator(
        MapSettings(820, 560, 91811, 6, 5, 7, temperature=35, max_altitude=92),
        lambda *_: None,
    )
    exhaust(engine)
    assert engine.river_paths
    for path in engine.river_paths:
        elevations = [engine._sample_grid(engine.elevation, x, y) for x, y in path]
        assert elevations[-1] <= elevations[0] + 0.04
        if len(elevations) > 2:
            non_rising = sum(1 for a, b in zip(elevations, elevations[1:]) if b <= a + 0.02)
            assert non_rising / (len(elevations) - 1) >= 0.72


def test_house_layouts_vary_and_remain_inside_shell():
    config = GenerationConfig()
    assets = AssetLibrary()
    signatures = set()
    counts = set()
    for seed in range(17, 25):
        building = Entity(
            f"house-{seed}", "building", "house", "House", 0, 0,
            width=66, height=48, metadata={"building_type": "house", "biome": "plains"},
        )
        gen = BuildingInteriorGenerator(building, 900, 680, seed, config, assets, lambda *_: None)
        exhaust(gen)
        rooms = [e for e in gen.entities if e.kind == "room"]
        counts.add(len(rooms))
        bounds = []
        for room in rooms:
            x1, y1, x2, y2 = room.metadata["bounds"]
            assert 0 < x1 < x2 < gen.width
            assert 0 < y1 < y2 < gen.height
            bounds.append((x1, y1, x2, y2, room.subtype))
        signatures.add(tuple(bounds))
    assert len(counts) >= 2
    assert len(signatures) >= 4


def test_square_and_hex_snapping():
    assert snap_point(13, 19, "Square", 40, 400, 300) == (20.0, 20.0)
    sx, sy = snap_point(87, 92, "Square", 40, 400, 300)
    assert (sx - 20) % 40 == 0 and (sy - 20) % 40 == 0

    hx, hy = snap_point(87, 92, "Hex", 48, 400, 300)
    hx2, hy2 = snap_point(hx, hy, "Hex", 48, 400, 300)
    assert abs(hx - hx2) < 1e-6 and abs(hy - hy2) < 1e-6
    assert 0 <= hx <= 400 and 0 <= hy <= 300


def test_local_project_save_restores_world_and_view():
    old = os.environ.get("CAMPAIGNFORGE_SAVES")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CAMPAIGNFORGE_SAVES"] = tmp
        try:
            config = GenerationConfig(temperature=67, max_altitude=44, road_min_per_settlement=1, road_max_per_settlement=4)
            state = CampaignState(config=config, title="Test Campaign")
            state.view_state = {"zoom": 1.75, "xview": 0.22, "yview": 0.31, "grid_type": "Hex", "grid_size": 52}
            root = Scene("root", "World", "world", Image.new("RGB", (160, 100), (1, 2, 3)), 99)
            child = Scene("town", "Goldwatch", "town", Image.new("RGB", (100, 80), (4, 5, 6)), 100, parent_id="root", source_rect=(20, 10, 90, 70))
            root.entities.append(Entity("town-marker", "settlement", "town", "Goldwatch", 55, 40, child_scene_id="town"))
            state.add_scene(root)
            state.add_scene(child)
            state.current_id = "town"
            target = save_path("continuation-test")
            save_campaign(str(target), state, AssetLibrary())

            listed = list_local_saves()
            assert any(item.path == target for item in listed)
            loaded, _assets, extraction = load_campaign(str(target))
            try:
                assert loaded.title == "Test Campaign"
                assert loaded.current_id == "town"
                assert set(loaded.scenes) == {"root", "town"}
                assert loaded.scenes["root"].entities[0].child_scene_id == "town"
                assert loaded.view_state["zoom"] == 1.75
                assert loaded.view_state["grid_type"] == "Hex"
                assert loaded.config.temperature == 67
                assert loaded.config.max_altitude == 44
                assert loaded.config.road_max_per_settlement == 4
            finally:
                shutil.rmtree(extraction, ignore_errors=True)
        finally:
            if old is None:
                os.environ.pop("CAMPAIGNFORGE_SAVES", None)
            else:
                os.environ["CAMPAIGNFORGE_SAVES"] = old


def run():
    test_temperature_and_altitude_change_world_character()
    test_roads_respect_terrain_and_degree_limits()
    test_rivers_follow_elevation()
    test_house_layouts_vary_and_remain_inside_shell()
    test_square_and_hex_snapping()
    test_local_project_save_restores_world_and_view()


if __name__ == "__main__":
    run()
    print("Version 2 terrain/save feature tests passed")
