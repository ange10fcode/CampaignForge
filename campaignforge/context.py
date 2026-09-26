from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .biomes import WATER_BIOMES, dominant_biome, point_segment_distance
from .model import Entity, Scene


MAJOR_LOCATION_SUBTYPES = {
    "castle",
    "fort",
    "cave",
    "wizard_tower",
    "mage_tower",
    "ocean_monument",
    "temple",
    "ruin",
    "dungeon_entrance",
    "mine",
    "dragon_lair",
    "bandit_camp",
    "ruined_city",
}


@dataclass
class SelectionContext:
    semantic: str
    biome: str
    focus_entity: Optional[Entity] = None
    focus_entities: list[Entity] = field(default_factory=list)
    road_angle: float = 0.0
    road_entrances: list[float] = field(default_factory=list)
    lod: int = 2
    selection_fraction: float = 0.0
    reason: str = ""


def _selection_fraction(scene: Scene, rect: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = rect
    return max(1, (x2 - x1) * (y2 - y1)) / max(1, scene.image.width * scene.image.height)


def _lod_for(scene: Scene, fraction: float, location_count: int) -> int:
    if scene.kind == "world":
        if fraction >= 0.35:
            return 0
        if fraction >= 0.10 or location_count >= 2:
            return 1
        return 2
    if scene.kind == "region":
        if fraction >= 0.25 or location_count >= 3:
            return 1
        return 2
    return 2


def _major_locations(entities: list[Entity]) -> list[Entity]:
    return [
        e
        for e in entities
        if e.kind == "settlement"
        or (e.kind in {"landmark", "building"} and e.subtype in MAJOR_LOCATION_SUBTYPES)
    ]


def resolve_selection(scene: Scene, rect: tuple[int, int, int, int]) -> SelectionContext:
    x1, y1, x2, y2 = rect
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    biome = dominant_biome(scene, rect)
    entities = [e for e in scene.entities if x1 <= e.x <= x2 and y1 <= e.y <= y2]
    settlements = [e for e in entities if e.kind == "settlement"]
    buildings = [e for e in entities if e.kind == "building"]
    rooms = [e for e in entities if e.kind == "room"]
    majors = _major_locations(entities)
    fraction = _selection_fraction(scene, rect)
    lod = _lod_for(scene, fraction, len(majors))

    floors = [e for e in entities if e.kind == "floor"]
    if scene.kind == "building_section" and floors:
        if len(floors) == 1:
            focus = floors[0]
            return SelectionContext("floor", biome, focus, [focus], lod=3, selection_fraction=fraction, reason="floor inside multi-floor building")
        focus = min(floors, key=lambda e: math.hypot(e.x - cx, e.y - cy))
        return SelectionContext("floor", biome, focus, [focus], lod=3, selection_fraction=fraction, reason="floor inside multi-floor building")
    if scene.kind in {"building", "floor"} and rooms:
        focus = min(rooms, key=lambda e: math.hypot(e.x - cx, e.y - cy))
        return SelectionContext("room", biome, focus, [focus], lod=3, selection_fraction=fraction, reason="room inside building")
    if scene.kind == "room":
        return SelectionContext("room", biome, lod=3, selection_fraction=fraction, reason="already inside a room")
    if scene.kind in {"cavern", "dragon_lair"}:
        return SelectionContext("terminal", biome, lod=3, selection_fraction=fraction, reason="terminal detailed location")
    if scene.kind in {"town", "castle", "mage_tower"} and buildings:
        if len(buildings) == 1:
            focus = buildings[0]
            return SelectionContext("building", biome, focus, [focus], lod=3, selection_fraction=fraction, reason="building inside location")
        if len(buildings) > 1:
            return SelectionContext("terrain", biome, focus_entities=buildings, lod=2, selection_fraction=fraction, reason="multiple buildings selected")

    if len(majors) >= 2:
        return SelectionContext(
            "region",
            biome,
            focus_entities=majors,
            lod=min(lod, 1),
            selection_fraction=fraction,
            reason=f"multi-location selection ({len(majors)} locations)",
        )

    if len(settlements) == 1:
        focus = settlements[0]
        focused = fraction < 0.12 or focus.contains(cx, cy, max(x2 - x1, y2 - y1) * 0.28)
        if focused:
            semantic = "castle" if focus.subtype == "castle" else "town"
            return SelectionContext(
                semantic,
                focus.metadata.get("biome", biome),
                focus,
                [focus],
                road_entrances=road_entrances_for_entity(scene, focus),
                lod=3,
                selection_fraction=fraction,
                reason="selection focused on settlement",
            )

    if len(majors) == 1:
        focus = majors[0]
        focused = fraction < 0.10 or focus.contains(cx, cy, max(x2 - x1, y2 - y1) * 0.24)
        if focused:
            ctx = context_for_entity(scene, focus)
            ctx.selection_fraction = fraction
            return ctx

    road = _closest_path(scene, cx, cy, "road")
    if road and road[0] < max(30, min(x2 - x1, y2 - y1) * 0.28):
        return SelectionContext(
            "road",
            biome,
            road_angle=road[1],
            lod=2,
            selection_fraction=fraction,
            reason="selection follows a road",
        )

    if biome in WATER_BIOMES:
        return SelectionContext("ocean", biome, lod=lod, selection_fraction=fraction, reason="water-dominant selection")

    if scene.kind in {"world", "region"}:
        return SelectionContext(
            "region",
            biome,
            focus_entities=majors,
            lod=lod,
            selection_fraction=fraction,
            reason="regional terrain selection",
        )
    return SelectionContext("terrain", biome, lod=2, selection_fraction=fraction, reason="local biome detail")


def context_for_entity(scene: Scene, entity: Entity) -> SelectionContext:
    biome = entity.metadata.get("biome") or scene.sample_biome(entity.x, entity.y)
    entrances = road_entrances_for_entity(scene, entity)
    if entity.kind == "floor":
        return SelectionContext("floor", biome, entity, [entity], lod=3, reason="building floor")
    if entity.kind == "settlement":
        if entity.subtype == "castle":
            return SelectionContext("castle", biome, entity, [entity], road_entrances=entrances, lod=3, reason="castle settlement")
        return SelectionContext("town", biome, entity, [entity], road_entrances=entrances, lod=3, reason="settlement")
    if entity.kind == "building":
        return SelectionContext("building", biome, entity, [entity], road_entrances=entrances, lod=3, reason="building")
    if entity.kind == "room":
        return SelectionContext("room", biome, entity, [entity], lod=3, reason="room")
    if entity.subtype == "fort":
        return SelectionContext("fort", biome, entity, [entity], road_entrances=entrances, lod=3, reason="fort")
    if entity.subtype == "castle":
        return SelectionContext("castle", biome, entity, [entity], road_entrances=entrances, lod=3, reason="castle")
    if entity.subtype in {"wizard_tower", "mage_tower"}:
        return SelectionContext("mage_tower", biome, entity, [entity], road_entrances=entrances, lod=3, reason="mage tower")
    if entity.subtype == "dragon_lair":
        return SelectionContext("dragon_lair", biome, entity, [entity], road_entrances=entrances, lod=3, reason="dragon lair")
    if entity.subtype == "bandit_camp":
        return SelectionContext("bandit_camp", biome, entity, [entity], road_entrances=entrances, lod=3, reason="bandit camp")
    if entity.subtype == "ruined_city":
        return SelectionContext("ruined_city", biome, entity, [entity], road_entrances=entrances, lod=3, reason="ruined settlement")
    if entity.subtype in {"cave", "cave_mouth", "dungeon_entrance", "mine"}:
        if scene.metadata.get("terminal") or scene.kind in {"cavern", "dragon_lair"}:
            return SelectionContext("terminal", biome, entity, [entity], road_entrances=entrances, lod=4, reason="terminal underground location")
        if scene.kind == "cave":
            return SelectionContext("cave_interior", biome, entity, [entity], road_entrances=entrances, lod=3, reason="entering cave interior")
        return SelectionContext("cave", biome, entity, [entity], road_entrances=entrances, lod=3, reason="cave or underground location")
    if entity.subtype in {"boat", "shipwreck", "sea_ruin", "ocean_monument", "island"}:
        return SelectionContext("ocean", biome, entity, [entity], lod=2, reason="ocean feature")
    return SelectionContext("terrain", biome, entity, [entity], lod=2, reason="local object")


def road_entrances_for_entity(scene: Scene, entity: Entity) -> list[float]:
    ref = entity.metadata.get("world_ref")
    angles: list[float] = []
    proximity = max(55.0, max(entity.width, entity.height) * 2.4)
    for path in scene.paths:
        if path.get("kind") not in {"road", "trail"}:
            continue
        pts = path.get("points", [])
        if len(pts) < 2:
            continue
        if ref and path.get("from_ref") == ref:
            x, y = pts[min(2, len(pts) - 1)]
            angles.append(math.atan2(y - entity.y, x - entity.x))
            continue
        if ref and path.get("to_ref") == ref:
            x, y = pts[max(0, len(pts) - 3)]
            angles.append(math.atan2(y - entity.y, x - entity.x))
            continue
        nearest_i = min(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - entity.x, pts[i][1] - entity.y))
        if math.hypot(pts[nearest_i][0] - entity.x, pts[nearest_i][1] - entity.y) > proximity:
            continue
        candidates = []
        if nearest_i > 0:
            candidates.append(pts[max(0, nearest_i - 2)])
        if nearest_i < len(pts) - 1:
            candidates.append(pts[min(len(pts) - 1, nearest_i + 2)])
        for x, y in candidates:
            if math.hypot(x - entity.x, y - entity.y) > 10:
                angles.append(math.atan2(y - entity.y, x - entity.x))
    return _dedupe_angles(angles)


def _dedupe_angles(angles: list[float], tolerance: float = math.radians(24)) -> list[float]:
    out: list[float] = []
    for angle in angles:
        normalized = math.atan2(math.sin(angle), math.cos(angle))
        if all(abs(math.atan2(math.sin(normalized - other), math.cos(normalized - other))) > tolerance for other in out):
            out.append(normalized)
    return out[:6]


def _closest_path(scene: Scene, x: float, y: float, kind: str):
    best = None
    for path in scene.paths:
        if path.get("kind") != kind:
            continue
        pts = path.get("points", [])
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            dist = point_segment_distance(x, y, ax, ay, bx, by)
            if best is None or dist < best[0]:
                best = (dist, math.atan2(by - ay, bx - ax))
    return best
