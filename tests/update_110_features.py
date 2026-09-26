from __future__ import annotations

import math

from PIL import Image, ImageChops

from campaignforge.assets import AssetLibrary
from campaignforge.context import context_for_entity
from campaignforge.experimental import render_experimental
from campaignforge.generators import (
    BanditCampGenerator,
    BuildingInteriorGenerator,
    BuildingSectionGenerator,
    DeepCaveGenerator,
    DetailSettings,
    DragonLairGenerator,
    FortGenerator,
    TownGenerator,
)
from campaignforge.model import Entity, GenerationConfig, Scene


def exhaust(gen):
    for _ in gen.generate():
        pass
    return gen


def settings(seed=101):
    return DetailSettings(900, 680, seed, density=7, structure_count=16, landmark_count=4)


def test_town_inherits_exact_major_roads():
    cfg=GenerationConfig(); assets=AssetLibrary()
    settlement=Entity("s","settlement","town","Roadtown",0,0,60,60,metadata={"biome":"plains"})
    gen=exhaust(TownGenerator(settlement, settings(10), cfg, assets, lambda *_:None, [0.0, math.pi]))
    major=[p for p in gen.paths if p.get("kind")=="road" and p.get("major")]
    assert len(major)==2
    assert gen.metadata["major_road_count"]==2
    no_parent=exhaust(TownGenerator(settlement, settings(11), cfg, assets, lambda *_:None, []))
    assert len([p for p in no_parent.paths if p.get("kind")=="road" and p.get("major")])==0


def test_special_locations_have_distinct_content():
    cfg=GenerationConfig(); assets=AssetLibrary()
    bandit=Entity("b","landmark","bandit_camp","Ragged Camp",0,0,45,45,metadata={"biome":"forest","camp_size":"medium"})
    bg=exhaust(BanditCampGenerator(bandit, settings(20), cfg, assets, lambda *_:None, [0.2]))
    assert any(e.subtype in {"tent","rough_hut"} for e in bg.entities)
    assert any(e.subtype=="bandit" for e in bg.entities)

    dragon=Entity("d","landmark","dragon_lair","Ash Maw",0,0,55,55,metadata={"biome":"mountain"})
    dg=exhaust(DragonLairGenerator(dragon, settings(21), cfg, assets, lambda *_:None))
    dragons=[e for e in dg.entities if e.subtype=="dragon"]
    assert len(dragons)==1 and dragons[0].metadata.get("large_creature")
    assert dragons[0].width > 100
    assert not any(e.kind=="building" and e.subtype=="house" for e in dg.entities)

    fort=Entity("f","landmark","fort","Greywatch",0,0,45,45,metadata={"biome":"hills"})
    fg=exhaust(FortGenerator(fort, settings(22), cfg, assets, lambda *_:None, [math.pi/2]))
    assert any(e.subtype=="barracks" for e in fg.entities)
    assert len([e for e in fg.entities if e.kind=="building"]) <= 6


def test_cave_depth_is_terminal():
    cfg=GenerationConfig(); assets=AssetLibrary()
    mouth=Entity("c","landmark","cave_mouth","Old Cave",0,0,80,60,metadata={"biome":"hills"})
    deep=exhaust(DeepCaveGenerator(mouth, settings(30), cfg, assets, lambda *_:None))
    assert deep.metadata.get("terminal") is True
    assert not any(e.subtype in {"cave","cave_mouth"} for e in deep.entities)
    scene=Scene("deep","Deep Cavern","cavern",deep.image,30,biome="mountain",semantic="cavern",entities=deep.entities,metadata=deep.metadata)
    dummy=Entity("x","landmark","cave","Another Cave",300,200)
    assert context_for_entity(scene,dummy).semantic=="terminal"


def test_multifloor_section_preserves_identity():
    cfg=GenerationConfig(); assets=AssetLibrary()
    building=Entity("keep","building","castle","Keep",0,0,150,90,metadata={"biome":"plains","building_type":"castle","floor_count":4,"entrance_angle":0.0})
    section=exhaust(BuildingSectionGenerator(building,1000,760,31,cfg,assets,lambda *_:None))
    floors=[e for e in section.entities if e.kind=="floor"]
    assert len(floors)==4
    assert all(e.metadata["source_building_id"]=="keep" for e in floors)
    assert section.metadata["persistent_geometry"]["width"]==150


def test_interior_furniture_is_movable_and_entrance_tracks_parent():
    cfg=GenerationConfig(); assets=AssetLibrary()
    building=Entity("h","building","house","Long House",0,0,120,48,metadata={"biome":"plains","building_type":"house","entrance_angle":math.pi})
    interior=exhaust(BuildingInteriorGenerator(building,1000,700,32,cfg,assets,lambda *_:None))
    rooms=[e for e in interior.entities if e.kind=="room"]
    objects=[e for e in interior.entities if e.kind=="object"]
    assert rooms and objects and all(o.movable for o in objects)
    ex,ey=interior.metadata["entrance"]
    # pi points toward the left wall, not the legacy bottom-center door.
    assert ex < 260
    geom=interior.metadata["persistent_geometry"]
    assert geom["source_width"]==120 and geom["source_height"]==48


def test_experimental_is_same_scene_visualization():
    img=Image.new("RGB",(500,360),(146,173,105))
    entity=Entity("tower","building","wizard_tower","Tower",260,160,60,60,metadata={"floor_count":4})
    scene=Scene("s","Same World","region",img,1,biome="plains",entities=[entity],paths=[{"kind":"road","points":[[20,300],[260,160],[470,80]]}],metadata={"biome_grid":[["plains"]*20 for _ in range(14)],"elevation_grid":[[.45+(x+y)/500 for x in range(20)] for y in range(14)]})
    before=entity.to_dict().copy()
    exp=render_experimental(scene,52,35,1.2)
    assert exp.size==img.size
    assert ImageChops.difference(exp,img).getbbox() is not None
    assert entity.to_dict()==before


def run():
    test_town_inherits_exact_major_roads()
    test_special_locations_have_distinct_content()
    test_cave_depth_is_terminal()
    test_multifloor_section_preserves_identity()
    test_interior_furniture_is_movable_and_entrance_tracks_parent()
    test_experimental_is_same_scene_visualization()


if __name__=="__main__":
    run(); print("Update 1.1.0 feature tests passed")
