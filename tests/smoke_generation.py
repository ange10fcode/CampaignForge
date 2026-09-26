from campaignforge.assets import AssetLibrary
from campaignforge.generators import BuildingInteriorGenerator, DetailSettings, OceanGenerator, TownGenerator, WorldSceneGenerator
from campaignforge.model import GenerationConfig, Scene
from campaignforge.world_engine import MapSettings


def run() -> None:
    status = lambda _message, _progress: None
    config = GenerationConfig()
    assets = AssetLibrary()
    world_gen = WorldSceneGenerator(MapSettings(600, 420, 12345, 5, 3, 5), config, status)
    for _ in world_gen.generate():
        pass
    world = Scene(
        "world", "World", "world", world_gen.image.copy(), 12345,
        biome=world_gen.biome, semantic=world_gen.semantic,
        entities=world_gen.entities, paths=world_gen.paths, metadata=world_gen.metadata,
    )
    assert world.entities
    settlement = world.entities[0]
    town_gen = TownGenerator(settlement, DetailSettings(900, 700, 23456, 7, 22, 5), config, assets, status)
    for _ in town_gen.generate():
        pass
    building = next(entity for entity in town_gen.entities if entity.kind == "building")
    interior = BuildingInteriorGenerator(building, 900, 700, 34567, config, assets, status)
    for _ in interior.generate():
        pass
    ocean = OceanGenerator("ocean", DetailSettings(800, 600, 45678, 7, 10, 5), config, assets, status)
    for _ in ocean.generate():
        pass
    assert all(entity.metadata.get("biome") == "ocean" for entity in ocean.entities)
    assert any(entity.kind == "room" for entity in interior.entities)
    assert any(entity.kind == "object" for entity in interior.entities)


if __name__ == "__main__":
    run()
    print("Generation smoke test passed")
