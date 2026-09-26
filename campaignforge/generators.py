from __future__ import annotations

import math
import random
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from typing import Generator, Optional

from PIL import Image, ImageDraw

from .world_engine import FantasyMapGenerator, MapSettings

from .assets import AssetLibrary
from .biomes import (
    BIOME_COLORS,
    LAND_BIOMES,
    WATER_BIOMES,
    compatible,
    dominant_biome,
    draw_biome_texture,
    make_biome_canvas,
)
from .model import Entity, GenerationConfig, Scene


@dataclass
class DetailSettings:
    width: int
    height: int
    seed: int
    density: int = 7
    structure_count: int = 18
    landmark_count: int = 7
    lod: int = 2
    selection_fraction: float = 0.0


BUILDING_PROFILES = {
    "house": {
        "floor": "wood",
        "rooms": ["living room", "kitchen", "bedroom", "storage"],
        "wall": (84, 65, 50),
    },
    "tavern": {
        "floor": "wood",
        "rooms": ["common room", "bar", "kitchen", "pantry", "guest room", "guest room"],
        "wall": (80, 59, 43),
    },
    "blacksmith": {
        "floor": "stone",
        "rooms": ["forge", "workshop", "storage", "office"],
        "wall": (74, 68, 61),
    },
    "library": {
        "floor": "wood",
        "rooms": ["stacks", "reading room", "study", "archive"],
        "wall": (78, 59, 44),
    },
    "temple": {
        "floor": "stone",
        "rooms": ["sanctuary", "vestry", "archive", "chapel"],
        "wall": (91, 88, 82),
    },
    "wizard_tower": {
        "floor": "stone",
        "rooms": ["study", "library", "alchemy lab", "ritual room"],
        "wall": (72, 65, 82),
    },
    "shop": {
        "floor": "wood",
        "rooms": ["sales floor", "counter", "storage", "office"],
        "wall": (82, 62, 46),
    },
    "castle": {
        "floor": "stone",
        "rooms": ["great hall", "guard room", "armory", "kitchen", "bedchamber", "storage"],
        "wall": (76, 75, 71),
    },
    "farmhouse": {
        "floor": "wood",
        "rooms": ["kitchen", "living room", "bedroom", "pantry"],
        "wall": (89, 66, 48),
    },
    "apothecary": {
        "floor": "wood",
        "rooms": ["sales floor", "alchemy lab", "storage", "office"],
        "wall": (75, 70, 52),
    },
    "bakery": {
        "floor": "stone",
        "rooms": ["sales floor", "kitchen", "pantry", "storage"],
        "wall": (91, 69, 49),
    },
    "stable": {
        "floor": "stone",
        "rooms": ["stable", "tack room", "storage"],
        "wall": (91, 69, 49),
    },
    "guild_hall": {
        "floor": "wood",
        "rooms": ["great hall", "office", "meeting room", "archive", "storage"],
        "wall": (75, 59, 48),
    },
    "warehouse": {
        "floor": "stone",
        "rooms": ["storage", "storage", "office", "loading room"],
        "wall": (81, 70, 58),
    },
    "government": {
        "floor": "stone",
        "rooms": ["great hall", "office", "archive", "meeting room"],
        "wall": (86, 82, 76),
    },
    "noble_house": {
        "floor": "wood",
        "rooms": ["great hall", "bedchamber", "study", "kitchen", "guest room", "storage"],
        "wall": (76, 59, 46),
    },
    "mage_shop": {
        "floor": "wood",
        "rooms": ["sales floor", "study", "alchemy lab", "storage"],
        "wall": (69, 61, 77),
    },
    "barracks": {
        "floor": "stone",
        "rooms": ["barracks", "armory", "storage", "office"],
        "wall": (76, 73, 67),
    },
}


class SceneGenerator:
    def __init__(self, status) -> None:
        self.status = status
        self.image = Image.new("RGB", (8, 8), (30, 30, 30))
        self.entities: list[Entity] = []
        self.paths: list[dict] = []
        self.metadata: dict = {}
        self.biome = "mixed"
        self.semantic = "region"

    def _progress(self, text: str, value: float) -> None:
        self.status(text, max(0.0, min(1.0, value)))


class WorldSceneGenerator(SceneGenerator):
    def __init__(self, settings: MapSettings, config: GenerationConfig, status) -> None:
        super().__init__(status)
        if not config.towns:
            settings = replace(settings, settlement_count=0)
        self.engine = FantasyMapGenerator(settings, status)
        self.image = self.engine.image
        self.settings = settings
        self.config = config
        self.semantic = "world"
        self.biome = "mixed"

    def generate(self) -> Generator[None, None, None]:
        for _ in self.engine.generate():
            self.image = self.engine.image
            yield
        self._extract_context()
        yield

    def _extract_context(self) -> None:
        e = self.engine
        grid = []
        for row in range(e.rows):
            out = []
            for col in range(e.cols):
                color = e._terrain_color(e.elevation[row][col], e.moisture[row][col], e.temperature[row][col])
                out.append(_color_to_biome(color))
            grid.append(out)
        self.metadata["biome_grid"] = grid
        self.metadata["biome_grid_size"] = [e.cols, e.rows]
        self.metadata["elevation_grid"] = [[round(v, 5) for v in row] for row in e.elevation]
        self.metadata["moisture_grid"] = [[round(v, 5) for v in row] for row in e.moisture]
        self.metadata["temperature_grid"] = [[round(v, 5) for v in row] for row in e.temperature]
        self.metadata["world_settings"] = {
            "temperature": self.settings.temperature,
            "max_altitude": self.settings.max_altitude,
            "moisture": self.settings.moisture,
            "auto_river_count": len(e.river_paths),
            "road_min_per_settlement": self.settings.road_min_per_settlement,
            "road_max_per_settlement": self.settings.road_max_per_settlement,
            "road_connection_chance": self.settings.road_connection_chance,
            "rare_ruin_chance": self.settings.rare_ruin_chance,
            "smooth_terrain": self.settings.smooth_terrain,
            "sea_level": round(e.sea_level, 5),
        }
        settlement_entities: list[Entity] = []
        for settlement in e.settlements:
            world_ref = str(uuid.uuid4())
            entity = Entity(
                id=str(uuid.uuid4()),
                kind="settlement",
                subtype=settlement.kind,
                name=settlement.name,
                x=settlement.x,
                y=settlement.y,
                width=48 if settlement.kind == "village" else (78 if settlement.kind == "city" else (68 if settlement.kind == "castle" else 58)),
                height=48 if settlement.kind == "village" else (78 if settlement.kind == "city" else (68 if settlement.kind == "castle" else 58)),
                metadata={
                    "biome": self._sample_engine_biome(settlement.x, settlement.y),
                    "world_ref": world_ref,
                    "location_type": settlement.kind,
                },
            )
            self.entities.append(entity)
            settlement_entities.append(entity)
        if self.config.roads:
            for path in e.road_paths:
                if len(path) < 2:
                    continue
                first = min(settlement_entities, key=lambda ent: math.hypot(ent.x - path[0][0], ent.y - path[0][1])) if settlement_entities else None
                last = min(settlement_entities, key=lambda ent: math.hypot(ent.x - path[-1][0], ent.y - path[-1][1])) if settlement_entities else None
                self.paths.append({
                    "id": str(uuid.uuid4()),
                    "kind": "road",
                    "major": True,
                    "from_ref": first.metadata.get("world_ref") if first else None,
                    "to_ref": last.metadata.get("world_ref") if last else None,
                    "points": [[round(x, 2), round(y, 2)] for x, y in path],
                })
        for path in e.river_paths:
            self.paths.append({"id": str(uuid.uuid4()), "kind": "river", "points": [[round(x, 2), round(y, 2)] for x, y in path]})
        for landmark in getattr(e, "landmarks", []):
            if landmark.kind == "wizard_tower" and not self.config.houses:
                continue
            if landmark.kind in {"ruin"} and not self.config.ruins:
                continue
            if landmark.kind in {"cave", "dragon_lair", "dungeon_entrance", "mine"} and not (self.config.underground or self.config.dungeons):
                continue
            world_ref = str(uuid.uuid4())
            entity = Entity(
                id=str(uuid.uuid4()),
                kind="landmark",
                subtype=landmark.kind,
                name=landmark.name,
                x=landmark.x,
                y=landmark.y,
                width=42,
                height=42,
                metadata={
                    "biome": self._sample_engine_biome(landmark.x, landmark.y),
                    "world_ref": world_ref,
                    "location_type": landmark.kind,
                    "building_type": "wizard_tower" if landmark.kind == "wizard_tower" else landmark.kind,
                },
            )
            self.entities.append(entity)
        self._connect_world_landmarks()

    def _connect_world_landmarks(self) -> None:
        if not self.config.roads:
            return
        draw = ImageDraw.Draw(self.image, "RGBA")
        anchors = [e for e in self.entities if e.kind == "settlement"]
        for landmark in [e for e in self.entities if e.kind == "landmark" and e.subtype in {"wizard_tower", "cave", "dragon_lair", "fort", "bandit_camp"}]:
            if not anchors:
                break
            target = min(anchors, key=lambda e: math.hypot(e.x - landmark.x, e.y - landmark.y))
            distance = math.hypot(target.x - landmark.x, target.y - landmark.y)
            diag = math.hypot(self.settings.width, self.settings.height)
            max_fraction = {"fort": .26, "wizard_tower": .22, "cave": .16, "dragon_lair": .15, "bandit_camp": .17}.get(landmark.subtype, .18)
            if distance > diag * max_fraction:
                continue
            source_stub = type("RoadAnchor", (), {"x": int(landmark.x), "y": int(landmark.y)})()
            target_stub = type("RoadAnchor", (), {"x": int(target.x), "y": int(target.y)})()
            points = self.engine._road_astar(source_stub, target_stub)
            if len(points) < 2:
                continue
            kind = "road" if landmark.subtype in {"wizard_tower", "fort"} else "trail"
            width = 3 if kind == "road" else 2
            draw.line(points, fill=(121, 91, 60, 175), width=width, joint="curve")
            self.paths.append({
                "id": str(uuid.uuid4()),
                "kind": kind,
                "major": landmark.subtype in {"wizard_tower", "fort"},
                "from_ref": landmark.metadata.get("world_ref"),
                "to_ref": target.metadata.get("world_ref"),
                "points": [[round(x, 2), round(y, 2)] for x, y in points],
            })

    def _sample_engine_biome(self, x: float, y: float) -> str:
        col = max(0, min(self.engine.cols - 1, int(x / self.engine.tile)))
        row = max(0, min(self.engine.rows - 1, int(y / self.engine.tile)))
        color = self.engine._terrain_color(self.engine.elevation[row][col], self.engine.moisture[row][col], self.engine.temperature[row][col])
        return _color_to_biome(color)


class ContextRegionGenerator(SceneGenerator):
    def __init__(
        self,
        parent: Scene,
        source_rect: tuple[int, int, int, int],
        settings: DetailSettings,
        config: GenerationConfig,
        assets: AssetLibrary,
        status,
    ) -> None:
        super().__init__(status)
        self.parent = parent
        self.rect = source_rect
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.biome = dominant_biome(parent, source_rect)
        self.semantic = "region"
        self.image = Image.new("RGB", (settings.width, settings.height), BIOME_COLORS.get(self.biome, BIOME_COLORS["plains"]))
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self._inherited_entities: list[Entity] = []
        self._inherited_paths: list[dict] = []

    def generate(self) -> Generator[None, None, None]:
        yield from self._terrain()
        yield from self._inherit_paths()
        yield from self._inherit_entities()
        yield from self._context_structures()
        yield from self._decorations()
        self._progress("Region complete", 1.0)
        yield

    def _map_parent(self, x: float, y: float) -> tuple[float, float]:
        x1, y1, x2, y2 = self.rect
        return (
            (x - x1) / max(1, x2 - x1) * self.settings.width,
            (y - y1) / max(1, y2 - y1) * self.settings.height,
        )

    def _map_child_to_parent(self, x: float, y: float) -> tuple[float, float]:
        x1, y1, x2, y2 = self.rect
        return (
            x1 + x / max(1, self.settings.width) * (x2 - x1),
            y1 + y / max(1, self.settings.height) * (y2 - y1),
        )

    def _terrain(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        cell = max(8, min(w, h) // 100)
        cols = math.ceil(w / cell)
        rows = math.ceil(h / cell)
        biome_grid: list[list[str]] = []
        for gy in range(rows):
            row = []
            for gx in range(cols):
                cx = min(w - 1, gx * cell + cell / 2)
                cy = min(h - 1, gy * cell + cell / 2)
                px, py = self._map_child_to_parent(cx, cy)
                biome = self.parent.sample_biome(px, py)
                row.append(biome)
                base = BIOME_COLORS.get(biome, BIOME_COLORS["plains"])
                jitter = self.rng.randint(-5, 5)
                color = tuple(max(0, min(255, c + jitter)) for c in base)
                self.draw.rectangle((gx * cell, gy * cell, min(w, (gx + 1) * cell), min(h, (gy + 1) * cell)), fill=color + (255,))
            biome_grid.append(row)
            if gy % 3 == 0:
                self._progress("Rebuilding terrain from parent context", .02 + .20 * gy / max(1, rows - 1))
                yield
        self.metadata["biome_grid"] = biome_grid
        self.metadata["source_semantic"] = self.parent.semantic
        self.metadata["source_rect"] = list(self.rect)
        self.metadata["aspect_preserved"] = True
        self.biome = Counter(b for row in biome_grid for b in row).most_common(1)[0][0]
        draw_biome_texture(self.image, self.biome, self.settings.seed + 77, 1.25)
        yield

    def _inherit_paths(self) -> Generator[None, None, None]:
        if not self.config.roads and all(p.get("kind") != "river" for p in self.parent.paths):
            return
        x1, y1, x2, y2 = self.rect
        margin = max(35, min(x2 - x1, y2 - y1) * 0.04)
        clip_rect = (x1 - margin, y1 - margin, x2 + margin, y2 + margin)
        for index, path in enumerate(self.parent.paths):
            kind = path.get("kind", "road")
            if kind in {"road", "trail"} and not self.config.roads:
                continue
            source_points = [(float(px), float(py)) for px, py in path.get("points", [])]
            clipped_segments = _clip_polyline_to_rect(source_points, clip_rect)
            for segment_index, source_segment in enumerate(clipped_segments):
                points = [self._map_parent(px, py) for px, py in source_segment]
                if len(points) < 2:
                    continue
                mapped_path = {k: v for k, v in path.items() if k != "points"}
                if len(clipped_segments) > 1:
                    mapped_path["id"] = f"{mapped_path.get('id', 'path')}-{segment_index}"
                mapped_path["points"] = [[round(x, 2), round(y, 2)] for x, y in points]
                mapped_path["inherited"] = True
                self.paths.append(mapped_path)
                if kind == "river":
                    self.draw.line(points, fill=(39, 103, 153, 255), width=max(7, self.settings.width // 180), joint="curve")
                    self.draw.line(points, fill=(92, 161, 193, 200), width=max(3, self.settings.width // 360), joint="curve")
                elif kind == "trail":
                    self.draw.line(points, fill=(108, 83, 58, 210), width=max(3, self.settings.width // 360), joint="curve")
                else:
                    outer = max(7, self.settings.width // 170)
                    inner = max(4, self.settings.width // 260)
                    self.draw.line(points, fill=(87, 66, 47, 190), width=outer, joint="curve")
                    self.draw.line(points, fill=(158, 121, 77, 255), width=inner, joint="curve")
            if clipped_segments:
                self._progress("Preserving parent roads, trails, and rivers", .25 + .08 * (index + 1) / max(1, len(self.parent.paths)))
                yield

    def _inherit_entities(self) -> Generator[None, None, None]:
        x1, y1, x2, y2 = self.rect
        candidates = [
            e for e in self.parent.entities
            if x1 <= e.x <= x2 and y1 <= e.y <= y2 and e.kind in {"settlement", "building", "landmark"}
        ]
        lod_scale = {0: .85, 1: 1.0, 2: 1.25, 3: 1.45}.get(self.settings.lod, 1.0)
        for i, entity in enumerate(candidates):
            x, y = self._map_parent(entity.x, entity.y)
            clone = Entity(
                id=str(uuid.uuid4()),
                kind=entity.kind,
                subtype=entity.subtype,
                name=entity.name,
                x=x,
                y=y,
                width=max(26, entity.width * lod_scale),
                height=max(26, entity.height * lod_scale),
                child_scene_id=entity.child_scene_id,
                metadata={
                    **entity.metadata,
                    "origin_entity_id": entity.id,
                    "lod": self.settings.lod,
                },
            )
            self.entities.append(clone)
            self._inherited_entities.append(clone)
            if clone.kind == "settlement":
                _draw_settlement_marker_lod(self.image, x, y, clone.subtype, clone.name, self.settings.lod)
            elif clone.subtype in {"castle", "fort", "cave", "dragon_lair", "bandit_camp", "ruined_city", "wizard_tower", "mage_tower", "ruin", "temple", "dungeon_entrance", "mine"}:
                _draw_location_marker(self.image, x, y, clone.subtype, clone.name, self.settings.lod, self.assets)
            elif clone.kind == "building" and self.settings.lod >= 2:
                _draw_topdown_building(self.image, clone, self.assets)
            self._progress("Preserving every selected location", .34 + .06 * (i + 1) / max(1, len(candidates)))
            yield
        self.metadata["inherited_location_count"] = len(candidates)
        self.metadata["lod"] = self.settings.lod
        self.metadata["parent_transform"] = {
            "source_rect": list(self.rect),
            "child_size": [self.settings.width, self.settings.height],
        }

    def _context_structures(self) -> Generator[None, None, None]:
        existing_towns = [e for e in self.entities if e.kind == "settlement"]
        if existing_towns and self.config.houses and self.settings.lod > 0:
            for town in existing_towns:
                if town.subtype == "castle":
                    count = 2 if self.settings.lod == 1 else 4
                    kinds = ["house", "house", "temple", "house"]
                else:
                    count = (3 if town.subtype == "village" else 5) if self.settings.lod == 1 else (8 if town.subtype == "village" else 13)
                    kinds = ["house"] * count
                radius_min = 24 if self.settings.lod == 1 else 38
                radius_max = 64 if self.settings.lod == 1 else (115 if town.subtype == "village" else 150)
                for j in range(count):
                    angle = (j / max(1, count)) * math.tau + self.rng.uniform(-.25, .25)
                    radius = self.rng.uniform(radius_min, radius_max)
                    x = town.x + math.cos(angle) * radius
                    y = town.y + math.sin(angle) * radius
                    if 20 < x < self.settings.width - 20 and 20 < y < self.settings.height - 20:
                        biome = self._child_biome(x, y)
                        if biome in LAND_BIOMES:
                            _draw_building(self.image, x, y, kinds[j % len(kinds)], self.assets, .48 if self.settings.lod == 1 else .72)
                self._progress(f"Representing {town.name} at LOD {self.settings.lod}", .42)
                yield
        lod_factor = {0: .08, 1: .28, 2: .65, 3: 1.0}.get(self.settings.lod, .65)
        desired = max(0, int(self.settings.structure_count * lod_factor) - len(existing_towns))
        placed = 0
        attempts = 0
        while placed < desired and attempts < max(80, desired * 100):
            attempts += 1
            x = self.rng.randint(35, self.settings.width - 35)
            y = self.rng.randint(35, self.settings.height - 35)
            biome = self._child_biome(x, y)
            kind = self._choose_structure(biome)
            if not kind:
                continue
            spacing = 90 if self.settings.lod <= 1 else 65
            if any(math.hypot(x - e.x, y - e.y) < spacing for e in self.entities):
                continue
            name = _structure_name(kind, self.rng)
            landmark_types = {"ruin", "cave", "dragon_lair", "bandit_camp", "ruined_city", "shipwreck", "sea_ruin", "ocean_monument", "dungeon_entrance", "mine", "wizard_tower", "fort"}
            entity_kind = "landmark" if kind in landmark_types else "building"
            entity = Entity(
                str(uuid.uuid4()), entity_kind, kind, name, x, y, 36, 36,
                metadata={
                    "biome": biome,
                    "building_type": _normalize_building_type(kind),
                    "generated_local": True,
                    "location_type": kind,
                    "world_ref": str(uuid.uuid4()),
                },
            )
            self.entities.append(entity)
            if kind in {"boat", "shipwreck", "sea_ruin", "ocean_monument"}:
                _draw_ocean_feature(self.image, x, y, kind, self.assets)
            elif kind in {"wizard_tower", "fort", "cave", "dragon_lair", "bandit_camp", "ruined_city"}:
                _draw_location_marker(self.image, x, y, kind, name, self.settings.lod, self.assets)
            else:
                _draw_building(self.image, x, y, kind, self.assets, .75 if self.settings.lod <= 1 else .9)
            placed += 1
            self._progress(f"Placing biome-valid {kind.replace('_', ' ')}", .44 + .18 * placed / max(1, desired))
            yield
        if self.config.roads:
            yield from self._connect_location_access()

    def _connect_location_access(self) -> Generator[None, None, None]:
        locations = [
            e for e in self.entities
            if e.metadata.get("generated_local") and e.subtype in {"fort", "castle", "wizard_tower", "cave", "dragon_lair", "bandit_camp"}
        ]
        anchors = [e for e in self.entities if e.kind == "settlement"]
        road_points = [
            (px, py) for path in self.paths if path.get("kind") in {"road", "trail"}
            for px, py in path.get("points", [])[::max(1, len(path.get("points", [])) // 12 or 1)]
        ]
        for i, loc in enumerate(locations):
            candidates: list[tuple[float, float]] = list(road_points)
            candidates.extend((e.x, e.y) for e in anchors if e.id != loc.id)
            if not candidates:
                continue
            tx, ty = min(candidates, key=lambda p: math.hypot(p[0] - loc.x, p[1] - loc.y))
            distance = math.hypot(tx - loc.x, ty - loc.y)
            if loc.subtype in {"cave", "dragon_lair", "bandit_camp"} and distance > min(self.settings.width, self.settings.height) * .32:
                continue
            kind = "road" if loc.subtype in {"fort", "castle", "wizard_tower"} else "trail"
            points = _curved_connection((loc.x, loc.y), (tx, ty), self.rng, 10 if kind == "road" else 7)
            self.paths.append({
                "id": str(uuid.uuid4()),
                "kind": kind,
                "major": loc.subtype in {"fort", "castle"},
                "from_ref": loc.metadata.get("world_ref"),
                "points": [[round(x, 2), round(y, 2)] for x, y in points],
            })
            width = max(3, self.settings.width // (250 if kind == "road" else 380))
            self.draw.line(points, fill=(145, 106, 69, 225), width=width, joint="curve")
            self._progress("Connecting major locations", .64 + .05 * (i + 1) / max(1, len(locations)))
            yield

    def _choose_structure(self, biome: str) -> Optional[str]:
        choices: list[str] = []
        if biome in WATER_BIOMES:
            if self.config.boats:
                choices += ["boat", "shipwreck"]
            if self.config.ocean_structures:
                choices += ["sea_ruin", "ocean_monument"]
        elif biome == "coast":
            if self.config.houses:
                choices += ["house", "inn", "dock"]
            if self.config.boats:
                choices += ["boat", "shipwreck"]
            if self.config.ruins:
                choices += ["ruin"]
        elif biome in {"forest", "dark_forest"}:
            if self.config.houses:
                choices += ["house", "witch_hut", "inn", "wizard_tower"]
            if self.config.ruins:
                choices += ["ruin", "cave"]
            if self.config.dungeons and self.rng.random() < .12:
                choices += ["dragon_lair"]
            if self.config.dungeons:
                choices += ["dungeon_entrance"]
            if self.config.underground:
                choices += ["mine"]
        elif biome in {"desert", "dry_plains"}:
            if self.config.houses:
                choices += ["house", "inn"]
            if self.config.ruins:
                choices += ["desert_temple", "ruin"]
            choices += ["oasis"]
        elif biome in {"mountain", "snow", "hills"}:
            if self.config.castles:
                choices += ["fort"]
            if self.config.ruins:
                choices += ["ruin", "cave"]
            if self.config.dungeons and self.rng.random() < .12:
                choices += ["dragon_lair"]
            if self.config.dungeons:
                choices += ["dungeon_entrance"]
            if self.config.underground:
                choices += ["mine"]
        else:
            if self.config.houses:
                choices += ["house", "farm", "inn", "temple", "windmill", "wizard_tower"]
            if self.config.castles:
                choices += ["fort"]
            if self.config.ruins:
                choices += ["ruin"]
        choices = [k for k in choices if compatible(k, biome)]
        return self.rng.choice(choices) if choices else None

    def _child_biome(self, x: float, y: float) -> str:
        grid = self.metadata.get("biome_grid")
        if not grid:
            return self.biome
        rows, cols = len(grid), len(grid[0])
        gx = min(cols - 1, max(0, int(x / self.settings.width * cols)))
        gy = min(rows - 1, max(0, int(y / self.settings.height * rows)))
        return grid[gy][gx]

    def _decorations(self) -> Generator[None, None, None]:
        if not self.config.decorations:
            return
        count = max(24, int(self.settings.width * self.settings.height / 8000 * self.settings.density / 5 * {0: .25, 1: .45, 2: 1.0, 3: 1.2}.get(self.settings.lod, 1.0)))
        for i in range(count):
            x = self.rng.randrange(12, self.settings.width - 12)
            y = self.rng.randrange(12, self.settings.height - 12)
            biome = self._child_biome(x, y)
            if biome in {"forest", "dark_forest"}:
                _draw_tree(self.image, x, y, self.rng.uniform(.55, 1.05), self.assets)
            elif biome in {"plains", "dry_plains", "coast"}:
                _draw_bush(self.image, x, y, self.rng.uniform(.7, 1.25))
            elif biome in {"desert"}:
                _draw_cactus(self.image, x, y)
            elif biome in {"hills", "mountain", "snow"}:
                _draw_rock(self.image, x, y, self.rng.randint(3, 8), self.assets)
            if i % 20 == 0:
                self._progress("Adding biome decorations", .70 + .25 * i / max(1, count))
                yield


class TownGenerator(SceneGenerator):
    def __init__(self, settlement: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.settlement = settlement
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.biome = settlement.metadata.get("biome", "plains")
        if self.biome in WATER_BIOMES:
            self.biome = "coast"
        self.semantic = "town"
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.15)
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self.road_paths: list[list[tuple[float, float]]] = []
        self.road_entrances = list(road_entrances or [])

    def generate(self) -> Generator[None, None, None]:
        yield from self._roads()
        yield from self._buildings()
        yield from self._town_details()
        self._make_biome_grid()
        self._progress("Town complete", 1.0)
        yield

    def _roads(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        cx, cy = w / 2, h / 2
        ring_rx = max(72, min(w, h) * .10)
        ring_ry = ring_rx * .78
        entrances = _dedupe_angles_local(list(self.road_entrances))[:6]
        ring = []
        for i in range(33):
            angle = i / 32 * math.tau
            ring.append((cx + math.cos(angle) * ring_rx, cy + math.sin(angle) * ring_ry))
        self.draw.line(ring, fill=(87, 65, 47, 190), width=20, joint="curve")
        self.draw.line(ring, fill=(160, 123, 78, 255), width=12, joint="curve")
        self.road_paths.append(ring)
        self.paths.append({
            "id": str(uuid.uuid4()),
            "kind": "street",
            "major": False,
            "points": [[round(x, 2), round(y, 2)] for x, y in ring],
        })
        self._progress("Building local ring street", .07)
        yield
        for i, angle in enumerate(entrances):
            border = _ray_to_border(cx, cy, angle, w, h, 3)
            ring_point = (cx + math.cos(angle) * ring_rx, cy + math.sin(angle) * ring_ry)
            points = _curved_connection(border, ring_point, self.rng, 20, wobble=9)
            self.road_paths.append(points)
            self.paths.append({
                "id": str(uuid.uuid4()),
                "kind": "road",
                "major": True,
                "entrance_angle": round(angle, 6),
                "points": [[round(x, 2), round(y, 2)] for x, y in points],
            })
            self.draw.line(points, fill=(86, 65, 47, 190), width=22, joint="curve")
            self.draw.line(points, fill=(160, 123, 78, 255), width=14, joint="curve")
            self._progress("Extending inherited roads to town gates", .08 + .16 * (i + 1) / max(1, len(entrances)))
            yield
        self.metadata["road_entrances"] = [round(a, 6) for a in entrances]
        self.metadata["major_road_count"] = len(entrances)

    def _buildings(self) -> Generator[None, None, None]:
        base = self.settings.structure_count + (14 if self.settlement.subtype != "village" else 7)
        target = max(18 if self.settlement.subtype == "village" else (62 if self.settlement.subtype == "city" else (44 if self.settlement.subtype == "town" else 34)), base)
        used: list[tuple[float, float, float, float]] = []
        candidates: list[tuple[float, float, float]] = []
        for path in self.road_paths:
            for idx in range(3, len(path) - 3, 2):
                a = path[max(0, idx - 1)]
                b = path[min(len(path) - 1, idx + 1)]
                dx, dy = b[0] - a[0], b[1] - a[1]
                ln = max(1.0, math.hypot(dx, dy))
                nx, ny = -dy / ln, dx / ln
                for side in (-1, 1):
                    offset = self.rng.uniform(34, 52)
                    bx = path[idx][0] + nx * offset * side
                    by = path[idx][1] + ny * offset * side
                    entrance_angle = math.atan2(path[idx][1] - by, path[idx][0] - bx)
                    candidates.append((bx, by, entrance_angle))
        self.rng.shuffle(candidates)
        special = ["tavern", "blacksmith", "shop", "temple", "library", "apothecary", "bakery", "stable", "guild_hall", "warehouse", "mage_shop"]
        if self.settlement.subtype == "castle" and self.config.castles:
            special.insert(0, "castle")
        placed = 0
        for x, y, entrance_angle in candidates:
            if placed >= target:
                break
            btype = special[placed] if placed < len(special) else self.rng.choices(
                ["house", "house", "house", "shop", "farmhouse", "noble_house", "warehouse"], weights=[7, 7, 7, 2, 2, 1, 1]
            )[0]
            if btype in {"house", "farmhouse"} and not self.config.houses:
                continue
            bw = self.rng.randint(30, 44) if btype != "castle" else 82
            bh = self.rng.randint(24, 38) if btype != "castle" else 68
            rect = (x - bw / 2 - 5, y - bh / 2 - 5, x + bw / 2 + 5, y + bh / 2 + 5)
            if x < 34 or y < 34 or x > self.settings.width - 34 or y > self.settings.height - 34:
                continue
            if any(_rect_overlap(rect, other) for other in used):
                continue
            used.append(rect)
            entity = Entity(
                str(uuid.uuid4()), "building", btype, _building_name(btype, self.rng), x, y, bw, bh,
                metadata={"biome": self.biome, "building_type": btype, "town": self.settlement.name, "entrance_angle": round(entrance_angle, 6), "floor_count": _building_floor_count(btype, bw, bh, self.rng)},
            )
            self.entities.append(entity)
            _draw_topdown_building(self.image, entity, self.assets)
            placed += 1
            self._progress("Clustering buildings along streets", .25 + .43 * placed / max(1, target))
            yield
        if self.config.npcs:
            for _ in range(max(4, placed // 4)):
                path = self.rng.choice(self.road_paths)
                x, y = self.rng.choice(path[2:-2])
                self.entities.append(Entity(
                    str(uuid.uuid4()), "npc", "townsperson",
                    self.rng.choice(["Villager", "Merchant", "Guard", "Traveler"]),
                    x + self.rng.randint(-10, 10), y + self.rng.randint(-10, 10), 22, 22,
                    movable=True, metadata={"biome": self.biome}
                ))
        yield

    def _town_details(self) -> Generator[None, None, None]:
        if not self.config.decorations:
            return
        count = max(80, self.settings.width * self.settings.height // 9000)
        for i in range(count):
            x = self.rng.randint(15, self.settings.width-15)
            y = self.rng.randint(15, self.settings.height-15)
            near_road = any(math.hypot(x-px, y-py) < 22 for path in self.road_paths for px, py in path[::3])
            if near_road and self.rng.random() < .09:
                subtype, label, ow, oh = self.rng.choice([
                    ("bench","Bench",38,18),("barrel","Barrel",22,22),("crate","Crate",25,22),
                    ("lamp","Street Lamp",18,28),("sign","Road Sign",24,28),("market_stall","Market Stall",52,34)
                ])
                self.entities.append(Entity(str(uuid.uuid4()),"object",subtype,label,x,y,ow,oh,movable=True,metadata={"biome":self.biome,"rotation":self.rng.choice([0,90,180,270]),"exterior":True}))
            if near_road:
                if self.config.carts and self.rng.random() < .05:
                    _draw_cart(self.image, x, y, self.assets)
                elif self.rng.random() < .18:
                    _draw_sign(self.image, x, y)
                continue
            if self.biome in {"forest", "dark_forest"}:
                _draw_tree(self.image, x, y, self.rng.uniform(.55, .9), self.assets)
            else:
                _draw_bush(self.image, x, y, self.rng.uniform(.6, 1.0))
            if i % 20 == 0:
                self._progress("Adding town details", .72 + .22 * i / count)
                yield

    def _make_biome_grid(self) -> None:
        self.metadata["biome_grid"] = [[self.biome for _ in range(50)] for _ in range(36)]
        self.metadata["settlement_name"] = self.settlement.name


class RoadEncounterGenerator(SceneGenerator):
    def __init__(self, biome: str, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, direction: float = 0.0) -> None:
        super().__init__(status)
        self.biome = biome if biome not in WATER_BIOMES else "coast"
        self.semantic = "road"
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.direction = direction
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.4)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def generate(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        angle = self.direction or self.rng.uniform(-.45, .45)
        cx, cy = w/2, h/2
        length = math.hypot(w, h)
        dx, dy = math.cos(angle), math.sin(angle)
        a = (cx-dx*length, cy-dy*length)
        b = (cx+dx*length, cy+dy*length)
        pts = []
        for i in range(31):
            t = i/30
            wob = math.sin(t*math.pi*3) * 18 + (self.rng.random()-.5)*8
            nx, ny = -dy, dx
            pts.append((a[0]+(b[0]-a[0])*t+nx*wob, a[1]+(b[1]-a[1])*t+ny*wob))
        self.paths.append({"kind":"road", "points":[[round(x,2),round(y,2)] for x,y in pts]})
        self.draw.line(pts, fill=(87, 65, 47, 210), width=42, joint="curve")
        self.draw.line(pts, fill=(159, 122, 78, 255), width=31, joint="curve")
        self._progress("Reconstructing the road", .25)
        yield
        if self.config.decorations:
            for i in range(100):
                p = self.rng.choice(pts[2:-2])
                nx, ny = -dy, dx
                side = self.rng.choice((-1,1))
                offset = self.rng.uniform(34, 130)
                x, y = p[0]+nx*offset*side, p[1]+ny*offset*side
                if 8 < x < w-8 and 8 < y < h-8:
                    if self.biome in {"forest","dark_forest"} and self.rng.random()<.45:
                        _draw_tree(self.image,x,y,self.rng.uniform(.65,1.1),self.assets)
                    else:
                        _draw_bush(self.image,x,y,self.rng.uniform(.6,1.2))
                if i % 20 == 0:
                    self._progress("Adding roadside vegetation", .30+.25*i/100)
                    yield
        if self.config.carts:
            for _ in range(self.rng.randint(1,3)):
                x,y = self.rng.choice(pts[8:-8])
                _draw_cart(self.image,x,y,self.assets)
                self.entities.append(Entity(str(uuid.uuid4()),"object","cart","Old Cart",x,y,34,24,metadata={"biome":self.biome}))
        _draw_sign(self.image, pts[7][0]+30, pts[7][1]+15)
        self.entities.append(Entity(str(uuid.uuid4()),"object","sign","Road Sign",pts[7][0]+30,pts[7][1]+15,20,28,metadata={"biome":self.biome}))
        if self.config.npcs:
            for _ in range(self.rng.randint(2,5)):
                x,y = self.rng.choice(pts[5:-5])
                self.entities.append(Entity(str(uuid.uuid4()),"npc","traveler",self.rng.choice(["Traveler","Guard","Merchant","Pilgrim"]),x+self.rng.randint(-15,15),y+self.rng.randint(-15,15),22,22,movable=True,metadata={"biome":self.biome}))
        self.metadata["biome_grid"] = [[self.biome for _ in range(50)] for _ in range(36)]
        self._progress("Road scene complete",1.0)
        yield


class OceanGenerator(SceneGenerator):
    def __init__(self, biome: str, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.biome = "deep_ocean" if biome == "deep_ocean" else "ocean"
        self.semantic = "ocean"
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.8)

    def generate(self) -> Generator[None, None, None]:
        count = max(4, self.settings.structure_count // 2)
        types = []
        if self.config.boats:
            types += ["boat", "boat", "shipwreck"]
        if self.config.ocean_structures:
            types += ["sea_ruin", "ocean_monument", "island"]
        for i in range(count):
            if not types:
                break
            kind = self.rng.choice(types)
            x = self.rng.randint(50,self.settings.width-50)
            y = self.rng.randint(50,self.settings.height-50)
            _draw_ocean_feature(self.image,x,y,kind,self.assets)
            entity_kind = "building" if kind in {"ocean_monument"} else "landmark"
            self.entities.append(Entity(str(uuid.uuid4()),entity_kind,kind,_structure_name(kind,self.rng),x,y,50,50,metadata={"biome":self.biome,"building_type":"ocean_monument" if kind=="ocean_monument" else kind}))
            self._progress("Adding ocean-specific features", .15+.65*(i+1)/count)
            yield
        if self.config.npcs and self.config.boats:
            for _ in range(2):
                self.entities.append(Entity(str(uuid.uuid4()),"npc","sailor","Sailor",self.rng.randint(60,self.settings.width-60),self.rng.randint(60,self.settings.height-60),22,22,movable=True,metadata={"biome":self.biome}))
        self.metadata["biome_grid"] = [[self.biome for _ in range(50)] for _ in range(36)]
        self._progress("Ocean scene complete",1.0)
        yield


class WildernessGenerator(SceneGenerator):
    def __init__(self, biome: str, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.biome = biome
        self.semantic = "terrain"
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.image = make_biome_canvas(settings.width, settings.height, biome, settings.seed, 1.5)

    def generate(self) -> Generator[None, None, None]:
        draw_biome_texture(self.image,self.biome,self.settings.seed+9,1.7)
        self._progress("Building local terrain",.18)
        yield
        d = ImageDraw.Draw(self.image,"RGBA")
        decor_count = max(70,self.settings.width*self.settings.height//7500)
        if self.config.decorations:
            for i in range(decor_count):
                x,y=self.rng.randrange(10,self.settings.width-10),self.rng.randrange(10,self.settings.height-10)
                if self.biome in {"forest","dark_forest"}:
                    _draw_tree(self.image,x,y,self.rng.uniform(.55,1.15),self.assets)
                elif self.biome in {"desert","dry_plains"}:
                    if self.rng.random()<.3: _draw_cactus(self.image,x,y)
                    else: _draw_rock(self.image,x,y,self.rng.randint(2,5),self.assets)
                elif self.biome in {"mountain","hills","snow"}:
                    _draw_rock(self.image,x,y,self.rng.randint(3,9),self.assets)
                else:
                    _draw_bush(self.image,x,y,self.rng.uniform(.55,1.1))
                if i%20==0:
                    self._progress("Adding local terrain details",.22+.38*i/decor_count)
                    yield
        regular_choices=[]
        abandoned_choices=[]
        if self.biome in {"forest","dark_forest"}:
            if self.config.houses: regular_choices += ["witch_hut","house"]
            if self.config.ruins: abandoned_choices += ["ruin"]
            if self.config.underground: regular_choices += ["cave"]
        elif self.biome in {"desert","dry_plains"}:
            regular_choices += ["oasis"]
            if self.config.ruins: abandoned_choices += ["desert_temple","ruin"]
        elif self.biome in {"mountain","hills","snow"}:
            if self.config.underground: regular_choices += ["cave","mine"]
            if self.config.castles: regular_choices += ["fort"]
            if self.config.dungeons: regular_choices += ["dungeon_entrance"]
            if self.config.ruins: abandoned_choices += ["ruin"]
        else:
            if self.config.houses: regular_choices += ["farm","house"]
            if self.config.dungeons: regular_choices += ["dungeon_entrance"]
            if self.config.ruins: abandoned_choices += ["ruin"]
        max_regular = min(2, max(0, self.settings.landmark_count // 4))
        if self.biome in {"plains", "forest", "dark_forest"}:
            max_regular = min(max_regular, 1)
        spawn_queue = [self.rng.choice(regular_choices) for _ in range(max_regular)] if regular_choices else []
        if self.biome in {"plains", "forest", "dark_forest"}:
            spawn_queue = [k for k in spawn_queue if k != "house" or self.rng.random() < .22]
        ruin_rolls = max(1, self.settings.landmark_count // 2)
        ruin_probability = max(0.0, min(0.25, self.config.rare_ruin_chance / 100.0))
        for _ in range(ruin_rolls):
            if abandoned_choices and self.rng.random() < ruin_probability:
                spawn_queue.append(self.rng.choice(abandoned_choices))
        for i,kind in enumerate(spawn_queue):
            if not compatible(kind,self.biome):
                continue
            x,y=self.rng.randint(60,self.settings.width-60),self.rng.randint(60,self.settings.height-60)
            _draw_building(self.image,x,y,kind,self.assets,.9)
            entity_kind = "building" if kind not in {"ruin","cave","dungeon_entrance","mine"} else "landmark"
            metadata={"biome":self.biome,"building_type":_normalize_building_type(kind),"abandoned": kind in {"ruin","desert_temple"}}
            self.entities.append(Entity(str(uuid.uuid4()),entity_kind,kind,_structure_name(kind,self.rng),x,y,44,44,metadata=metadata))
            self._progress("Adding biome-valid structures",.65+.25*(i+1)/max(1,len(spawn_queue)))
            yield
        if self.config.wildlife and self.biome not in WATER_BIOMES:
            animals = ["Deer", "Wolf", "Boar"] if self.biome in {"forest", "dark_forest", "hills"} else ["Rabbit", "Fox"]
            for _ in range(self.rng.randint(2, 5)):
                self.entities.append(Entity(str(uuid.uuid4()), "npc", "wildlife", self.rng.choice(animals), self.rng.randint(30, self.settings.width-30), self.rng.randint(30, self.settings.height-30), 20, 20, movable=True, metadata={"biome": self.biome, "wildlife": True}))
        self.metadata["biome_grid"]=[[self.biome for _ in range(50)] for _ in range(36)]
        self._progress("Terrain scene complete",1.0)
        yield



class CastleGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.location = location
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.biome = location.metadata.get("biome", "plains")
        if self.biome in WATER_BIOMES:
            self.biome = "coast"
        self.semantic = "castle"
        self.road_entrances = list(road_entrances or [])
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.05)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def generate(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        cx, cy = w / 2, h / 2
        wall_w = min(w * .58, 720)
        wall_h = min(h * .56, 540)
        x1, y1, x2, y2 = cx - wall_w / 2, cy - wall_h / 2, cx + wall_w / 2, cy + wall_h / 2
        self.draw.rectangle((x1, y1, x2, y2), fill=(150, 146, 132, 55), outline=(69, 66, 60, 255), width=16)
        for tx, ty in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
            self.draw.ellipse((tx-18, ty-18, tx+18, ty+18), fill=(135, 132, 123, 255), outline=(64, 61, 57, 255), width=4)
        self._progress("Building castle walls", .18)
        yield
        entrances = _dedupe_angles_local(self.road_entrances)
        if not entrances:
            entrances = [math.pi / 2]
        gate_angle = entrances[0]
        gate = _ray_rect_intersection(cx, cy, gate_angle, (x1, y1, x2, y2))
        if gate is None:
            gate = (cx, y2)
        for i, angle in enumerate(entrances[:4]):
            border = _ray_to_border(cx, cy, angle, w, h, 3)
            target = gate if i == 0 else _ray_rect_intersection(cx, cy, angle, (x1, y1, x2, y2)) or gate
            pts = _curved_connection(border, target, self.rng, 18, wobble=7)
            self.paths.append({"id": str(uuid.uuid4()), "kind": "road", "major": True, "entrance_angle": round(angle, 6), "points": [[round(x,2),round(y,2)] for x,y in pts]})
            self.draw.line(pts, fill=(90, 67, 47, 210), width=24, joint="curve")
            self.draw.line(pts, fill=(158, 122, 78, 255), width=14, joint="curve")
            self._progress("Connecting inherited castle roads", .20 + .12 * (i + 1) / max(1, len(entrances[:4])))
            yield
        keep = Entity(str(uuid.uuid4()), "building", "castle", f"{self.location.name} Keep", cx, cy-35, 150, 105, metadata={"biome": self.biome, "building_type": "castle", "location": self.location.name, "floor_count": self.rng.randint(3,5), "entrance_angle": round(gate_angle,6)})
        support = [
            Entity(str(uuid.uuid4()), "building", "blacksmith", "Castle Forge", cx-wall_w*.25, cy+wall_h*.20, 70, 48, metadata={"biome": self.biome, "building_type": "blacksmith"}),
            Entity(str(uuid.uuid4()), "building", "barracks", "Barracks", cx+wall_w*.24, cy+wall_h*.20, 92, 52, metadata={"biome": self.biome, "building_type": "barracks", "floor_count": 2}),
            Entity(str(uuid.uuid4()), "building", "temple", "Castle Chapel", cx+wall_w*.25, cy-wall_h*.23, 66, 52, metadata={"biome": self.biome, "building_type": "temple"}),
        ]
        self.entities.append(keep)
        self.entities.extend(support)
        for i, building in enumerate([keep, *support]):
            _draw_topdown_building(self.image, building, self.assets)
            self._progress("Placing castle buildings", .38 + .30 * (i + 1) / 4)
            yield
        courtyard = _curved_connection(gate, (cx, cy), self.rng, 12, wobble=2)
        self.draw.line(courtyard, fill=(154, 126, 91, 255), width=18, joint="curve")
        self.paths.append({"id": str(uuid.uuid4()), "kind": "street", "major": False, "points": [[round(x,2),round(y,2)] for x,y in courtyard]})
        if self.config.npcs:
            for _ in range(8):
                self.entities.append(Entity(str(uuid.uuid4()), "npc", "guard", "Guard", self.rng.uniform(x1+35, x2-35), self.rng.uniform(y1+35, y2-35), 24, 24, movable=True, metadata={"biome": self.biome}))
        self.metadata["biome_grid"] = [[self.biome for _ in range(50)] for _ in range(36)]
        self.metadata["road_entrances"] = [round(a, 6) for a in entrances]
        self._progress("Castle complete", 1.0)
        yield


class MageTowerGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.location = location
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.biome = location.metadata.get("biome", "plains")
        if self.biome in WATER_BIOMES:
            self.biome = "coast"
        self.semantic = "mage_tower"
        self.road_entrances = list(road_entrances or [])
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.35)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def generate(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        cx, cy = w / 2, h / 2
        if self.config.decorations:
            for i in range(max(45, w*h//18000)):
                x, y = self.rng.randrange(15, w-15), self.rng.randrange(15, h-15)
                if self.biome in {"forest", "dark_forest"}:
                    _draw_tree(self.image, x, y, self.rng.uniform(.55, .95), self.assets)
                else:
                    _draw_bush(self.image, x, y, self.rng.uniform(.55, .95))
                if i % 30 == 0:
                    self._progress("Building tower grounds", .12 + .12*i/max(1,w*h//18000))
                    yield
        entrances = _dedupe_angles_local(self.road_entrances)
        if not entrances:
            entrances = [self.rng.choice([0.0, math.pi/2, math.pi, -math.pi/2])]
        for i, angle in enumerate(entrances[:2]):
            border = _ray_to_border(cx, cy, angle, w, h, 3)
            target = (cx + math.cos(angle)*80, cy + math.sin(angle)*80)
            pts = _curved_connection(border, target, self.rng, 20, wobble=8)
            self.paths.append({"id": str(uuid.uuid4()), "kind": "trail", "major": False, "entrance_angle": round(angle,6), "points": [[round(x,2),round(y,2)] for x,y in pts]})
            self.draw.line(pts, fill=(131, 96, 64, 230), width=10, joint="curve")
            self._progress("Carrying the tower approach into detail", .28 + .10*(i+1)/max(1,len(entrances[:2])))
            yield
        tower = Entity(str(uuid.uuid4()), "building", "wizard_tower", self.location.name, cx, cy, 104, 104, metadata={"biome": self.biome, "building_type": "wizard_tower", "location": self.location.name})
        annex = Entity(str(uuid.uuid4()), "building", "house", "Apprentice Quarters", cx+145, cy+75, 62, 46, metadata={"biome": self.biome, "building_type": "house"})
        self.entities.extend([tower, annex])
        _draw_location_marker(self.image, cx, cy, "wizard_tower", "", 3, self.assets)
        _draw_topdown_building(self.image, annex, self.assets)
        self._progress("Raising the mage tower", .62)
        yield
        if self.config.npcs:
            self.entities.append(Entity(str(uuid.uuid4()), "npc", "mage", "Mage", cx+40, cy+45, 26, 26, movable=True, metadata={"biome": self.biome}))
            self.entities.append(Entity(str(uuid.uuid4()), "npc", "apprentice", "Apprentice", cx+115, cy+110, 24, 24, movable=True, metadata={"biome": self.biome}))
        self.metadata["biome_grid"] = [[self.biome for _ in range(50)] for _ in range(36)]
        self.metadata["road_entrances"] = [round(a,6) for a in entrances]
        self._progress("Mage tower grounds complete", 1.0)
        yield


class CaveGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.location = location
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.biome = location.metadata.get("biome", "hills")
        if self.biome in WATER_BIOMES:
            self.biome = "coast"
        self.semantic = "cave"
        self.road_entrances = list(road_entrances or [])
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.45)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def generate(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        cx, cy = w*.58, h*.48
        if self.config.decorations:
            for i in range(max(80, w*h//12000)):
                x, y = self.rng.randrange(12, w-12), self.rng.randrange(12, h-12)
                if self.biome in {"forest", "dark_forest"} and self.rng.random() < .45:
                    _draw_tree(self.image, x, y, self.rng.uniform(.55, 1.0), self.assets)
                else:
                    _draw_rock(self.image, x, y, self.rng.randint(3, 9), self.assets)
                if i % 35 == 0:
                    self._progress("Building rocky cave terrain", .10 + .20*i/max(1,w*h//12000))
                    yield
        for r in range(78, 24, -12):
            self.draw.arc((cx-r*1.4, cy-r, cx+r*1.4, cy+r), 180, 360, fill=(61, 55, 48, 255), width=8)
        self.draw.ellipse((cx-70, cy-4, cx+70, cy+82), fill=(35, 33, 30, 255), outline=(74, 66, 57, 255), width=5)
        self._progress("Carving cave entrance", .44)
        yield
        entrances = _dedupe_angles_local(self.road_entrances)
        if not entrances:
            entrances = [math.pi]
        for i, angle in enumerate(entrances[:2]):
            border = _ray_to_border(cx, cy, angle, w, h, 3)
            target = (cx-10, cy+48)
            pts = _curved_connection(border, target, self.rng, 18, wobble=10)
            self.paths.append({"id": str(uuid.uuid4()), "kind": "trail", "major": False, "entrance_angle": round(angle,6), "points": [[round(x,2),round(y,2)] for x,y in pts]})
            self.draw.line(pts, fill=(121, 91, 63, 220), width=9, joint="curve")
            self._progress("Preserving cave approach trails", .52 + .10*(i+1)/max(1,len(entrances[:2])))
            yield
        self.entities.append(Entity(str(uuid.uuid4()), "landmark", "cave_mouth", self.location.name, cx, cy+38, 110, 72, metadata={"biome": self.biome, "location_type": "cave"}))
        if self.config.npcs:
            names = ["Explorer", "Hunter", "Guard"]
            for _ in range(self.rng.randint(1,3)):
                self.entities.append(Entity(str(uuid.uuid4()), "npc", "traveler", self.rng.choice(names), cx+self.rng.randint(-140,80), cy+self.rng.randint(80,170), 24, 24, movable=True, metadata={"biome": self.biome}))
        self.metadata["biome_grid"] = [[self.biome for _ in range(50)] for _ in range(36)]
        self.metadata["road_entrances"] = [round(a,6) for a in entrances]
        self._progress("Cave scene complete", 1.0)
        yield


class FortGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.location = location
        self.settings = settings
        self.config = config
        self.assets = assets
        self.rng = random.Random(settings.seed)
        self.biome = location.metadata.get("biome", "plains")
        if self.biome in WATER_BIOMES:
            self.biome = "coast"
        self.semantic = "fort"
        self.road_entrances = _dedupe_angles_local(list(road_entrances or []))
        self.image = make_biome_canvas(settings.width, settings.height, self.biome, settings.seed, 1.0)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def generate(self) -> Generator[None, None, None]:
        w, h = self.settings.width, self.settings.height
        cx, cy = w * .52, h * .50
        fw = min(w * .42, 500)
        fh = min(h * .38, 360)
        x1, y1, x2, y2 = cx-fw/2, cy-fh/2, cx+fw/2, cy+fh/2
        wall = (92, 76, 58, 255)
        fill = (122, 103, 78, 70)
        self.draw.rectangle((x1,y1,x2,y2), fill=fill, outline=wall, width=13)
        for tx,ty in ((x1,y1),(x2,y1),(x1,y2),(x2,y2)):
            self.draw.rectangle((tx-13,ty-13,tx+13,ty+13), fill=(109,91,69,255), outline=(58,50,42,255), width=3)
        entrances = self.road_entrances[:3]
        gate_angle = entrances[0] if entrances else math.pi/2
        gate = _ray_to_rect_border(cx, cy, gate_angle, (x1,y1,x2,y2))
        self.draw.ellipse((gate[0]-14,gate[1]-14,gate[0]+14,gate[1]+14), fill=(177,129,73,255), outline=(60,48,38,255), width=3)
        self._progress("Building compact defensive fort", .20)
        yield
        buildings = [
            ("barracks", "Barracks", cx-85, cy-38, 120, 70),
            ("blacksmith", "Armory & Forge", cx+76, cy-38, 92, 64),
            ("stable", "Stable", cx-72, cy+70, 110, 60),
            ("house", "Commander's Quarters", cx+78, cy+66, 92, 60),
        ]
        for i,(kind,name,x,y,bw,bh) in enumerate(buildings):
            entity=Entity(str(uuid.uuid4()),"building",kind,name,x,y,bw,bh,metadata={"biome":self.biome,"building_type":kind,"floor_count":_building_floor_count(kind,bw,bh,self.rng),"location":self.location.name})
            self.entities.append(entity)
            _draw_topdown_building(self.image,entity,self.assets)
            self._progress("Placing fort buildings", .30+.28*(i+1)/len(buildings))
            yield
        for i,angle in enumerate(entrances):
            border=_ray_to_border(cx,cy,angle,w,h,3)
            pts=_curved_connection(border,gate if i==0 else (cx,cy),self.rng,18,wobble=6)
            self.paths.append({"id":str(uuid.uuid4()),"kind":"road","major":i==0,"entrance_angle":round(angle,6),"points":[[round(x,2),round(y,2)] for x,y in pts]})
            self.draw.line(pts,fill=(86,65,47,190),width=16,joint="curve")
            self.draw.line(pts,fill=(157,119,76,255),width=9,joint="curve")
            yield
        if self.config.npcs:
            for _ in range(self.rng.randint(5,9)):
                self.entities.append(Entity(str(uuid.uuid4()),"npc","guard","Guard",cx+self.rng.randint(-150,150),cy+self.rng.randint(-120,120),24,24,movable=True,metadata={"biome":self.biome}))
        self.metadata.update({"biome_grid":[[self.biome for _ in range(45)] for _ in range(34)],"road_entrances":[round(a,6) for a in entrances],"fort_scale":"small"})
        self._progress("Fort complete",1.0)
        yield


class BanditCampGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.location=location; self.settings=settings; self.config=config; self.assets=assets
        self.rng=random.Random(settings.seed)
        self.biome=location.metadata.get("biome","plains")
        if self.biome in WATER_BIOMES: self.biome="coast"
        self.semantic="bandit_camp"
        self.road_entrances=_dedupe_angles_local(list(road_entrances or []))
        self.image=make_biome_canvas(settings.width,settings.height,self.biome,settings.seed,1.35)
        self.draw=ImageDraw.Draw(self.image,"RGBA")

    def generate(self)->Generator[None,None,None]:
        w,h=self.settings.width,self.settings.height; cx,cy=w*.52,h*.50
        radius=min(w,h)*.24
        pal=[]
        for i in range(24):
            a=i/24*math.tau
            rr=radius*self.rng.uniform(.82,1.08)
            pal.append((cx+math.cos(a)*rr,cy+math.sin(a)*rr*.78))
        pal.append(pal[0])
        self.draw.line(pal,fill=(83,58,39,230),width=9,joint="curve")
        self._progress("Raising rough palisade",.15); yield
        self.draw.ellipse((cx-20,cy-14,cx+20,cy+14),fill=(61,53,43,220),outline=(40,35,31,255),width=2)
        self.draw.polygon(((cx-8,cy+7),(cx,cy-16),(cx+9,cy+7)),fill=(224,126,48,240),outline=(116,58,30,240))
        camp_size=str(self.location.metadata.get("camp_size") or self.rng.choice(["small","small","medium","large"]))
        tent_count={"small":4,"medium":7,"large":11}.get(camp_size,6)
        for i in range(tent_count):
            a=self.rng.uniform(0,math.tau); r=self.rng.uniform(radius*.25,radius*.72)
            x=cx+math.cos(a)*r; y=cy+math.sin(a)*r*.72
            if camp_size=="large" and i in {0,1}:
                kind="rough_hut"; bw,bh=62,44
                self.draw.rectangle((x-bw/2,y-bh/2,x+bw/2,y+bh/2),fill=(132,103,68,255),outline=(65,48,35,255),width=3)
                self.draw.polygon(((x-bw/2-4,y-bh/2),(x,y-bh/2-18),(x+bw/2+4,y-bh/2)),fill=(88,62,43,255),outline=(60,44,34,255))
            else:
                kind="tent"; bw,bh=48,38
                self.draw.polygon(((x-bw/2,y+bh/2),(x,y-bh/2),(x+bw/2,y+bh/2)),fill=(145,124,91,245),outline=(62,49,38,255))
                self.draw.line((x,y-bh/2,x,y+bh/2),fill=(90,68,45,210),width=2)
            self.entities.append(Entity(str(uuid.uuid4()),"building",kind,"Bandit Tent" if kind=="tent" else "Rough Hut",x,y,bw,bh,metadata={"biome":self.biome,"building_type":"house","camp":self.location.name,"floor_count":1}))
            if i%2==0: yield
        for j,(sub,name,ox,oy) in enumerate([
            ("loot","Loot Pile",-75,15),("weapon_rack","Weapon Rack",78,8),("prison","Prison Pen",58,86),("supplies","Supplies",-92,-62)
        ]):
            x,y=cx+ox,cy+oy
            self.entities.append(Entity(str(uuid.uuid4()),"object",sub,name,x,y,34,28,movable=True,metadata={"biome":self.biome,"rotation":self.rng.choice([0,90,180,270])}))
        entrances=self.road_entrances[:2]
        for i,a in enumerate(entrances):
            border=_ray_to_border(cx,cy,a,w,h,3)
            pts=_curved_connection(border,(cx,cy),self.rng,16,wobble=9)
            self.paths.append({"id":str(uuid.uuid4()),"kind":"trail","major":False,"entrance_angle":round(a,6),"points":[[round(x,2),round(y,2)] for x,y in pts]})
            self.draw.line(pts,fill=(111,83,56,220),width=8,joint="curve")
        if self.config.npcs:
            count={"small":4,"medium":7,"large":11}.get(camp_size,6)
            for _ in range(count):
                self.entities.append(Entity(str(uuid.uuid4()),"npc","bandit","Bandit",cx+self.rng.randint(int(-radius*.65),int(radius*.65)),cy+self.rng.randint(int(-radius*.48),int(radius*.48)),24,24,movable=True,metadata={"biome":self.biome}))
        self.metadata.update({"biome_grid":[[self.biome for _ in range(48)] for _ in range(34)],"camp_size":camp_size,"road_entrances":[round(a,6) for a in entrances]})
        self._progress("Bandit camp complete",1.0); yield


class DragonLairGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]] = None) -> None:
        super().__init__(status)
        self.location=location; self.settings=settings; self.config=config; self.assets=assets
        self.rng=random.Random(settings.seed); self.semantic="dragon_lair"; self.biome="mountain"
        self.image=Image.new("RGB",(settings.width,settings.height),(42,39,36)); self.draw=ImageDraw.Draw(self.image,"RGBA")

    def generate(self)->Generator[None,None,None]:
        w,h=self.settings.width,self.settings.height; cx,cy=w*.55,h*.52
        chambers=[(cx,cy,min(w,h)*.24),(cx-w*.23,cy+h*.05,min(w,h)*.15),(cx+w*.18,cy-h*.18,min(w,h)*.13)]
        for i,(x,y,r) in enumerate(chambers):
            self.draw.ellipse((x-r*1.35,y-r,x+r*1.35,y+r),fill=(65,59,53,255),outline=(28,27,26,255),width=10)
            if i: self.draw.line((cx,cy,x,y),fill=(64,58,52,255),width=max(40,int(r*.55)))
            yield
        for _ in range(16):
            x=cx+self.rng.randint(-int(w*.23),int(w*.23)); y=cy+self.rng.randint(-int(h*.21),int(h*.21))
            self.draw.line((x-8,y,x+8,y),fill=(209,203,177,150),width=3)
            self.draw.ellipse((x-3,y-3,x+3,y+3),outline=(209,203,177,150),width=1)
        for _ in range(22):
            x=cx+self.rng.randint(-90,100); y=cy+self.rng.randint(25,115)
            self.draw.ellipse((x-4,y-3,x+4,y+3),fill=(216,168,53,230),outline=(126,89,34,180))
        self.entities.append(Entity(str(uuid.uuid4()),"object","treasure","Dragon Hoard",cx+30,cy+90,110,70,movable=False,metadata={"biome":"underground"}))
        dragon=Entity(str(uuid.uuid4()),"npc","dragon","Dragon",cx,cy-5,150,105,movable=True,metadata={"biome":"underground","large_creature":True,"tiles_w":3,"tiles_h":2})
        self.entities.append(dragon)
        if self.config.npcs:
            for _ in range(self.rng.randint(2,5)):
                self.entities.append(Entity(str(uuid.uuid4()),"npc","cave_creature",self.rng.choice(["Kobold","Drake","Cultist"]),cx+self.rng.randint(-220,220),cy+self.rng.randint(-160,180),24,24,movable=True,metadata={"biome":"underground"}))
        self.metadata.update({"biome_grid":[["mountain" for _ in range(48)] for _ in range(34)],"terminal":True,"location_type":"dragon_lair","dragon_entity_id":dragon.id})
        self._progress("Dragon lair complete",1.0); yield


class DeepCaveGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.location=location; self.settings=settings; self.config=config; self.assets=assets
        self.rng=random.Random(settings.seed); self.semantic="cavern"; self.biome="mountain"
        self.image=Image.new("RGB",(settings.width,settings.height),(40,39,37)); self.draw=ImageDraw.Draw(self.image,"RGBA")

    def generate(self)->Generator[None,None,None]:
        w,h=self.settings.width,self.settings.height
        chambers=[]
        for i in range(5):
            x=self.rng.randint(int(w*.18),int(w*.82)); y=self.rng.randint(int(h*.18),int(h*.82)); rx=self.rng.randint(90,180); ry=self.rng.randint(70,145)
            chambers.append((x,y,rx,ry))
        for i,(x,y,rx,ry) in enumerate(chambers):
            self.draw.ellipse((x-rx,y-ry,x+rx,y+ry),fill=(68,64,59,255),outline=(26,25,24,255),width=12)
            if i:
                px,py,_,_=chambers[i-1]; self.draw.line((px,py,x,y),fill=(66,62,57,255),width=60)
            self._progress("Carving deep caverns",.10+.30*(i+1)/len(chambers)); yield
        wx,wy,wrx,wry=chambers[-1]
        self.draw.ellipse((wx-wrx*.75,wy-wry*.45,wx+wrx*.75,wy+wry*.45),fill=(48,92,112,220),outline=(79,128,145,240),width=3)
        for _ in range(55):
            x=self.rng.randint(20,w-20); y=self.rng.randint(20,h-20)
            if self.rng.random()<.55:
                self.draw.polygon(((x,y-12),(x-6,y+7),(x+6,y+7)),fill=(102,96,87,220),outline=(56,53,49,180))
            else:
                _draw_rock(self.image,x,y,self.rng.randint(3,8),self.assets)
        rx,ry,_,_=chambers[1]
        self.draw.rectangle((rx-55,ry-38,rx+55,ry+38),outline=(115,109,96,220),width=8)
        self.draw.line((rx-55,ry,rx+55,ry),fill=(115,109,96,160),width=4)
        self.entities.append(Entity(str(uuid.uuid4()),"object","treasure","Ancient Cache",rx+20,ry+20,42,30,movable=True,metadata={"biome":"underground"}))
        if self.config.npcs:
            creatures=[("Giant Spider",34),("Cave Lizard",30),("Goblin",24),("Underdark Beast",42)]
            for _ in range(self.rng.randint(4,8)):
                name,size=self.rng.choice(creatures); x,y,_,_=self.rng.choice(chambers)
                self.entities.append(Entity(str(uuid.uuid4()),"npc","cave_creature",name,x+self.rng.randint(-55,55),y+self.rng.randint(-45,45),size,size,movable=True,metadata={"biome":"underground","large_creature":size>36}))
        self.metadata.update({"biome_grid":[["mountain" for _ in range(48)] for _ in range(34)],"terminal":True,"location_type":"cavern"})
        self._progress("Deep cave complete",1.0); yield


class RuinedCityGenerator(SceneGenerator):
    def __init__(self, location: Entity, settings: DetailSettings, config: GenerationConfig, assets: AssetLibrary, status, road_entrances: Optional[list[float]]=None) -> None:
        super().__init__(status)
        self.location=location; self.settings=settings; self.config=config; self.assets=assets
        self.rng=random.Random(settings.seed); self.biome=location.metadata.get("biome","plains"); self.semantic="ruined_city"
        self.image=make_biome_canvas(settings.width,settings.height,self.biome,settings.seed,1.15); self.draw=ImageDraw.Draw(self.image,"RGBA")
        self.road_entrances=_dedupe_angles_local(list(road_entrances or []))

    def generate(self)->Generator[None,None,None]:
        w,h=self.settings.width,self.settings.height; cx,cy=w/2,h/2
        entrances=self.road_entrances[:4]
        for a in entrances:
            border=_ray_to_border(cx,cy,a,w,h,3); pts=_curved_connection(border,(cx,cy),self.rng,18,wobble=12)
            self.paths.append({"id":str(uuid.uuid4()),"kind":"road","major":True,"entrance_angle":round(a,6),"points":[[round(x,2),round(y,2)] for x,y in pts]})
            for k in range(0,len(pts)-1,3):
                self.draw.line(pts[k:k+2],fill=(113,88,61,170),width=12)
        count=max(18,self.settings.structure_count)
        for i in range(count):
            x=self.rng.randint(70,w-70); y=self.rng.randint(70,h-70); bw=self.rng.randint(34,62); bh=self.rng.randint(28,52)
            self.draw.rectangle((x-bw/2,y-bh/2,x+bw/2,y+bh/2),fill=(126,116,101,150),outline=(72,65,58,230),width=3)
            if self.rng.random()<.7:
                self.draw.polygon(((x-bw/2,y-bh/2),(x,y-bh*.9),(x+bw/2,y-bh/2)),fill=(78,67,58,160))
            if self.rng.random()<.75:
                self.draw.rectangle((x+self.rng.randint(-int(bw/3),int(bw/3)),y+self.rng.randint(-int(bh/3),int(bh/3)),x+bw/2+3,y+bh/2+3),fill=BIOME_COLORS.get(self.biome,BIOME_COLORS["plains"])+(210,))
            self.entities.append(Entity(str(uuid.uuid4()),"building","ruined_house",f"Ruined Building {i+1}",x,y,bw,bh,metadata={"biome":self.biome,"building_type":"house","abandoned":True,"floor_count":1}))
            if i%4==0: yield
        for _ in range(45):
            x=self.rng.randint(20,w-20); y=self.rng.randint(20,h-20); _draw_rock(self.image,x,y,self.rng.randint(2,6),self.assets)
        self.metadata.update({"biome_grid":[[self.biome for _ in range(48)] for _ in range(34)],"location_type":"ruined_city","road_entrances":[round(a,6) for a in entrances]})
        self._progress("Ruined settlement complete",1.0); yield


class BuildingSectionGenerator(SceneGenerator):
    def __init__(self, building: Entity, width: int, height: int, seed: int, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.building=building; self.width=width; self.height=height; self.seed=seed; self.config=config; self.assets=assets
        self.rng=random.Random(seed); self.biome=building.metadata.get("biome","plains"); self.semantic="building_section"
        self.building_type=_normalize_building_type(building.metadata.get("building_type") or building.subtype)
        self.floor_count=max(2,int(building.metadata.get("floor_count") or _building_floor_count(self.building_type,building.width,building.height,self.rng)))
        self.image=make_biome_canvas(width,height,self.biome,seed+53,.6); self.draw=ImageDraw.Draw(self.image,"RGBA")

    def generate(self)->Generator[None,None,None]:
        w,h=self.width,self.height; margin_x=max(120,w//7); margin_y=max(70,h//10)
        bw=w-2*margin_x; bh=h-2*margin_y
        floor_h=bh/self.floor_count
        wall=(72,62,54,255)
        self.draw.rectangle((margin_x,margin_y,w-margin_x,h-margin_y),fill=(191,172,137,230),outline=wall,width=12)
        stair_x=margin_x+bw*.78
        for i in range(self.floor_count):
            top=margin_y+(self.floor_count-1-i)*floor_h; bottom=top+floor_h
            if i>0: self.draw.line((margin_x,top,w-margin_x,top),fill=wall,width=7)
            self.draw.line((stair_x-45,bottom-18,stair_x+45,top+18),fill=(96,68,48,255),width=6)
            floor=Entity(str(uuid.uuid4()),"floor","floor",f"Floor {i+1}",w/2,(top+bottom)/2,bw-28,max(35,floor_h-18),metadata={"biome":self.biome,"building_type":self.building_type,"floor_index":i+1,"floor_count":self.floor_count,"source_building_id":self.building.id,"original_width":self.building.width,"original_height":self.building.height,"entrance_angle":self.building.metadata.get("entrance_angle",math.pi/2),"floor":BUILDING_PROFILES.get(self.building_type,BUILDING_PROFILES["house"])["floor"]})
            self.entities.append(floor)
            self.draw.text((margin_x+18,(top+bottom)/2),f"Floor {i+1}",anchor="lm",fill=(50,42,35,255))
            self._progress("Building floor section",.12+.66*(i+1)/self.floor_count); yield
        self.metadata.update({"biome_grid":[[self.biome for _ in range(40)] for _ in range(30)],"building_type":self.building_type,"floor_count":self.floor_count,"persistent_geometry":{"width":self.building.width,"height":self.building.height,"entrance_angle":self.building.metadata.get("entrance_angle")}})
        self._progress("Building section complete",1.0); yield


class BuildingInteriorGenerator(SceneGenerator):
    def __init__(self, building: Entity, width: int, height: int, seed: int, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.building = building
        self.width = width
        self.height = height
        self.seed = seed
        self.config = config
        self.assets = assets
        self.rng = random.Random(seed)
        self.biome = building.metadata.get("biome", "plains")
        self.semantic = "building"
        self.building_type = _normalize_building_type(building.metadata.get("building_type") or building.subtype)
        self.profile = BUILDING_PROFILES.get(self.building_type, BUILDING_PROFILES["house"])
        self.image = make_biome_canvas(width, height, self.biome, seed + 40, .8)
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self.room_rects: list[tuple[int, int, int, int, str]] = []
        self.doorways: list[tuple[str, int, int, int, int]] = []

    def generate(self) -> Generator[None, None, None]:
        yield from self._shell_and_rooms()
        yield from self._furnish()
        self.metadata["biome_grid"] = [[self.biome for _ in range(40)] for _ in range(30)]
        self.metadata["building_type"] = self.building_type
        self.metadata["floor_index"] = int(self.building.metadata.get("floor_index", 1))
        self.metadata["floor_count"] = int(self.building.metadata.get("floor_count", 1))
        self.metadata["persistent_geometry"] = {
            "source_width": float(self.building.metadata.get("original_width", self.building.width)),
            "source_height": float(self.building.metadata.get("original_height", self.building.height)),
            "entrance_angle": float(self.building.metadata.get("entrance_angle", math.pi / 2)),
        }
        self.metadata["layout_variant"] = self.seed % 9973
        self.metadata["room_count"] = len(self.room_rects)
        self._progress("Building interior complete", 1.0)
        yield

    def _room_count(self) -> int:
        area = max(1.0, float(self.building.width) * float(self.building.height))
        if self.building_type == "house":
            if area < 2200:
                return self.rng.randint(1, 2)
            if area < 3800:
                return self.rng.randint(2, 4)
            return self.rng.randint(3, 5)
        ranges = {
            "farmhouse": (2, 5), "tavern": (4, 6), "blacksmith": (2, 4), "library": (3, 5),
            "temple": (2, 4), "wizard_tower": (3, 5), "shop": (2, 4), "castle": (5, 6),
        }
        lo, hi = ranges.get(self.building_type, (2, min(5, len(self.profile["rooms"]))))
        return self.rng.randint(lo, hi)

    def _partition(self, rect: tuple[int, int, int, int], count: int) -> list[tuple[int, int, int, int]]:
        rects = [rect]
        min_span = max(95, min(self.width, self.height) // 8)
        while len(rects) < count:
            candidates = sorted(enumerate(rects), key=lambda item: (item[1][2]-item[1][0])*(item[1][3]-item[1][1]), reverse=True)
            split_done = False
            for idx, (x1, y1, x2, y2) in candidates:
                w, h = x2-x1, y2-y1
                vertical = w > h * 1.15 or (w > min_span * 2 and self.rng.random() < .55)
                if vertical and w >= min_span * 2:
                    cut = int(x1 + w * self.rng.uniform(.36, .64))
                    a, b = (x1,y1,cut,y2), (cut,y1,x2,y2)
                elif h >= min_span * 2:
                    cut = int(y1 + h * self.rng.uniform(.36, .64))
                    a, b = (x1,y1,x2,cut), (x1,cut,x2,y2)
                elif w >= min_span * 2:
                    cut = int(x1 + w * self.rng.uniform(.40, .60))
                    a, b = (x1,y1,cut,y2), (cut,y1,x2,y2)
                else:
                    continue
                rects.pop(idx)
                rects.extend([a,b])
                split_done = True
                break
            if not split_done:
                break
        return rects

    @staticmethod
    def _adjacent_door(a: tuple[int,int,int,int], b: tuple[int,int,int,int]) -> tuple[str,int,int,int,int] | None:
        ax1,ay1,ax2,ay2=a; bx1,by1,bx2,by2=b
        if abs(ax2-bx1) <= 1 or abs(bx2-ax1) <= 1:
            x = ax2 if abs(ax2-bx1) <= 1 else ax1
            lo, hi = max(ay1,by1), min(ay2,by2)
            if hi-lo >= 50:
                y=(lo+hi)//2
                return ("v",x,y-14,x,y+14)
        if abs(ay2-by1) <= 1 or abs(by2-ay1) <= 1:
            y = ay2 if abs(ay2-by1) <= 1 else ay1
            lo, hi = max(ax1,bx1), min(ax2,bx2)
            if hi-lo >= 50:
                x=(lo+hi)//2
                return ("h",x-14,y,x+14,y)
        return None

    def _connected_doors(self, rects: list[tuple[int,int,int,int]]) -> list[tuple[str,int,int,int,int]]:
        edges=[]
        for i in range(len(rects)):
            for j in range(i+1,len(rects)):
                door=self._adjacent_door(rects[i],rects[j])
                if door:
                    edges.append((self.rng.random(),i,j,door))
        parent=list(range(len(rects)))
        def find(x):
            while parent[x]!=x:
                parent[x]=parent[parent[x]]; x=parent[x]
            return x
        doors=[]
        for _,i,j,door in sorted(edges):
            a,b=find(i),find(j)
            if a==b: continue
            parent[b]=a
            doors.append(door)
        return doors

    def _shell_and_rooms(self) -> Generator[None, None, None]:
        margin = max(60, min(self.width, self.height) // 11)
        available_w = self.width - margin * 2
        available_h = self.height - margin * 2
        source_aspect = max(.65, min(1.75, float(self.building.width) / max(1.0, float(self.building.height))))
        shell_w = min(available_w, int(available_h * source_aspect))
        shell_h = min(available_h, int(shell_w / source_aspect))
        shell_w = max(int(available_w * .62), shell_w)
        shell_h = max(int(available_h * .62), shell_h)
        x1 = (self.width - shell_w)//2; y1=(self.height-shell_h)//2
        x2=x1+shell_w; y2=y1+shell_h
        wall = self.profile["wall"]
        floor_kind = self.profile["floor"]
        _fill_floor(self.image, (x1,y1,x2,y2), floor_kind, self.assets, self.seed)
        self.draw.rectangle((x1,y1,x2,y2), outline=wall+(255,), width=14)
        count = self._room_count()
        rects = self._partition((x1,y1,x2,y2), count)
        names = list(self.profile["rooms"])
        if self.building_type == "house":
            essential = ["living room", "bedroom", "kitchen", "storage"]
            names = essential + [n for n in names if n not in essential]
        if len(names) < len(rects):
            names += [f"room {i+1}" for i in range(len(rects)-len(names))]
        if self.building_type != "house":
            self.rng.shuffle(names)
        names=names[:len(rects)]
        for i,((rx1,ry1,rx2,ry2),name) in enumerate(zip(rects,names)):
            self.room_rects.append((rx1,ry1,rx2,ry2,name))
            self.draw.rectangle((rx1,ry1,rx2,ry2), outline=wall+(255,), width=7)
            cx=(rx1+rx2)//2; cy=(ry1+ry2)//2
            self.entities.append(Entity(str(uuid.uuid4()),"room",name.replace(" ","_"),name.title(),cx,cy,max(20,rx2-rx1-14),max(20,ry2-ry1-14),metadata={"bounds":[rx1,ry1,rx2,ry2],"building_type":self.building_type,"biome":self.biome,"floor":floor_kind}))
            self._progress("Creating varied connected rooms", .08 + .30*(i+1)/max(1,len(rects)))
            yield
        self.doorways = self._connected_doors(rects)
        for orientation,dx1,dy1,dx2,dy2 in self.doorways:
            if orientation == "v":
                self.draw.rectangle((dx1-6,dy1,dx2+6,dy2), fill=(185,133,75,255))
            else:
                self.draw.rectangle((dx1,dy1-6,dx2,dy2+6), fill=(185,133,75,255))
        entrance_angle = float(self.building.metadata.get("entrance_angle", math.pi / 2))
        door = _ray_rect_intersection((x1+x2)/2, (y1+y2)/2, entrance_angle, (x1,y1,x2,y2))
        if door is None:
            door = ((x1+x2)/2, y2)
        ex, ey = door
        if abs(ex-x1) < 3 or abs(ex-x2) < 3:
            self.draw.rectangle((ex-8,ey-20,ex+8,ey+20),fill=(178,124,69,255))
        else:
            self.draw.rectangle((ex-20,ey-8,ex+20,ey+8),fill=(178,124,69,255))
        self.metadata["entrance"] = [round(ex,2), round(ey,2)]
        self.metadata["entrance_angle"] = round(entrance_angle, 6)
        yield

    def _furnish(self) -> Generator[None, None, None]:
        if not self.config.furniture:
            return
        for i,(x1,y1,x2,y2,name) in enumerate(self.room_rects):
            specs = _furniture_specs((x1+18,y1+18,x2-18,y2-18), name, self.building_type, self.rng, detail=False)
            for subtype, label, x, y, fw, fh, rotation in specs:
                self.entities.append(Entity(
                    str(uuid.uuid4()), "object", subtype, label, x, y, fw, fh,
                    movable=True,
                    metadata={
                        "biome": self.biome,
                        "building_type": self.building_type,
                        "room": name,
                        "rotation": rotation,
                        "asset_slot": _furniture_asset_slot(subtype),
                    },
                ))
            self._progress(f"Furnishing {name}", .45 + .48*(i+1)/max(1,len(self.room_rects)))
            yield


class RoomGenerator(SceneGenerator):
    def __init__(self, room: Entity, width: int, height: int, seed: int, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.room=room
        self.width=width
        self.height=height
        self.seed=seed
        self.config=config
        self.assets=assets
        self.rng=random.Random(seed)
        self.biome=room.metadata.get("biome","plains")
        self.semantic="room"
        self.image=Image.new("RGB",(width,height),(168,145,109))

    def generate(self)->Generator[None,None,None]:
        floor=self.room.metadata.get("floor","wood")
        _fill_floor(self.image,(0,0,self.width,self.height),floor,self.assets,self.seed)
        d=ImageDraw.Draw(self.image,"RGBA")
        d.rectangle((8,8,self.width-9,self.height-9),outline=(71,58,48,255),width=16)
        self._progress("Expanding room",.25)
        yield
        if self.config.furniture:
            for subtype, label, x, y, fw, fh, rotation in _furniture_specs(
                (42,42,self.width-42,self.height-42),
                self.room.name.lower(),
                self.room.metadata.get("building_type","house"),
                self.rng,
                detail=True,
            ):
                self.entities.append(Entity(
                    str(uuid.uuid4()), "object", subtype, label, x, y, fw, fh,
                    movable=True,
                    metadata={
                        "biome": self.biome,
                        "building_type": self.room.metadata.get("building_type","house"),
                        "room": self.room.name,
                        "rotation": rotation,
                        "asset_slot": _furniture_asset_slot(subtype),
                    },
                ))
        self._progress("Room detail complete",1.0)
        yield



def _clip_polyline_to_rect(
    points: list[tuple[float, float]],
    rect: tuple[float, float, float, float],
) -> list[list[tuple[float, float]]]:
    if len(points) < 2:
        return []
    x_min, y_min, x_max, y_max = rect

    def clip_segment(a: tuple[float, float], b: tuple[float, float]):
        x0, y0 = a
        x1, y1 = b
        dx, dy = x1 - x0, y1 - y0
        p = (-dx, dx, -dy, dy)
        q = (x0 - x_min, x_max - x0, y0 - y_min, y_max - y0)
        u0, u1 = 0.0, 1.0
        for pi, qi in zip(p, q):
            if abs(pi) < 1e-9:
                if qi < 0:
                    return None
                continue
            t = qi / pi
            if pi < 0:
                u0 = max(u0, t)
            else:
                u1 = min(u1, t)
            if u0 > u1:
                return None
        return ((x0 + u0 * dx, y0 + u0 * dy), (x0 + u1 * dx, y0 + u1 * dy))

    result: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for a, b in zip(points, points[1:]):
        clipped = clip_segment(a, b)
        if clipped is None:
            if len(current) >= 2:
                result.append(current)
            current = []
            continue
        start, end = clipped
        if not current:
            current = [start, end]
        elif math.hypot(current[-1][0] - start[0], current[-1][1] - start[1]) <= 1e-4:
            if math.hypot(current[-1][0] - end[0], current[-1][1] - end[1]) > 1e-4:
                current.append(end)
        else:
            if len(current) >= 2:
                result.append(current)
            current = [start, end]
    if len(current) >= 2:
        result.append(current)
    return result


def _dedupe_angles_local(angles: list[float], tolerance: float = math.radians(22)) -> list[float]:
    out: list[float] = []
    for angle in angles:
        angle = math.atan2(math.sin(angle), math.cos(angle))
        if all(abs(math.atan2(math.sin(angle-other), math.cos(angle-other))) > tolerance for other in out):
            out.append(angle)
    return out


def _ray_to_border(cx: float, cy: float, angle: float, width: float, height: float, margin: float = 0) -> tuple[float, float]:
    dx, dy = math.cos(angle), math.sin(angle)
    candidates: list[float] = []
    if dx > 1e-6:
        candidates.append((width - margin - cx) / dx)
    elif dx < -1e-6:
        candidates.append((margin - cx) / dx)
    if dy > 1e-6:
        candidates.append((height - margin - cy) / dy)
    elif dy < -1e-6:
        candidates.append((margin - cy) / dy)
    positive = [t for t in candidates if t >= 0]
    t = min(positive) if positive else 0
    return cx + dx*t, cy + dy*t


def _ray_rect_intersection(cx: float, cy: float, angle: float, rect: tuple[float, float, float, float]) -> Optional[tuple[float, float]]:
    x1, y1, x2, y2 = rect
    dx, dy = math.cos(angle), math.sin(angle)
    hits: list[tuple[float, float, float]] = []
    if abs(dx) > 1e-7:
        for x in (x1, x2):
            t = (x-cx)/dx
            y = cy + dy*t
            if t >= 0 and y1 <= y <= y2:
                hits.append((t, x, y))
    if abs(dy) > 1e-7:
        for y in (y1, y2):
            t = (y-cy)/dy
            x = cx + dx*t
            if t >= 0 and x1 <= x <= x2:
                hits.append((t, x, y))
    if not hits:
        return None
    _, x, y = min(hits, key=lambda item: item[0])
    return x, y




def _ray_to_rect_border(cx: float, cy: float, angle: float, rect: tuple[float, float, float, float]) -> tuple[float, float]:
    hit = _ray_rect_intersection(cx, cy, angle, rect)
    return hit if hit is not None else ((rect[0] + rect[2]) / 2, rect[3])
def _curved_connection(a: tuple[float, float], b: tuple[float, float], rng: random.Random, steps: int = 16, wobble: float = 10) -> list[tuple[float, float]]:
    ax, ay = a
    bx, by = b
    dx, dy = bx-ax, by-ay
    length = max(1.0, math.hypot(dx, dy))
    nx, ny = -dy/length, dx/length
    bend = rng.uniform(-wobble, wobble)
    phase = rng.uniform(0, math.tau)
    points: list[tuple[float, float]] = []
    for i in range(max(2, steps)+1):
        t = i/max(2, steps)
        offset = math.sin(math.pi*t)*bend + math.sin(t*math.tau*1.5+phase)*wobble*.16
        points.append((ax+dx*t+nx*offset, ay+dy*t+ny*offset))
    return points


def _draw_settlement_marker_lod(image: Image.Image, x: float, y: float, kind: str, name: str, lod: int) -> None:
    if kind == "castle":
        _draw_location_marker(image, x, y, "castle", name, lod, None)
        return
    d = ImageDraw.Draw(image, "RGBA")
    ink = (62, 48, 36, 255)
    if lod <= 0:
        r = 8 if kind == "village" else 11
        d.ellipse((x-r, y-r, x+r, y+r), fill=(212, 190, 140, 245), outline=ink, width=2)
    elif lod == 1:
        offsets = [(-9, 4), (7, 5), (0, -7)] if kind == "village" else [(-13, -5), (4, -8), (-5, 10), (14, 7)]
        for ox, oy in offsets:
            d.rectangle((x+ox-5, y+oy-4, x+ox+5, y+oy+5), fill=(207, 184, 139, 255), outline=ink, width=1)
            d.polygon(((x+ox-6, y+oy-4), (x+ox, y+oy-10), (x+ox+6, y+oy-4)), fill=(119, 70, 52, 255), outline=ink)
    else:
        _draw_settlement_marker(image, x, y, kind, "")
    if name:
        d.text((x, y+20+(3 if lod>=2 else 0)), name, anchor="ma", fill=(48, 38, 30, 255), stroke_width=2, stroke_fill=(235, 221, 185, 220))


def _draw_location_marker(image: Image.Image, x: float, y: float, kind: str, name: str, lod: int, assets: Optional[AssetLibrary]) -> None:
    d = ImageDraw.Draw(image, "RGBA")
    ink = (58, 47, 39, 255)
    scale = .75 if lod <= 1 else 1.0
    if kind in {"castle", "fort"}:
        w, h = 25*scale, 19*scale
        d.rectangle((x-w, y-h, x+w, y+h), fill=(153, 149, 137, 255), outline=ink, width=max(2, int(3*scale)))
        for tx in (x-w, x+w):
            d.rectangle((tx-7*scale, y-h-7*scale, tx+7*scale, y+h), fill=(137, 134, 126, 255), outline=ink, width=2)
    elif kind in {"wizard_tower", "mage_tower"}:
        if assets and _paste_asset(image, assets.get_slot("building.house"), x, y, int(54*scale), int(70*scale)):
            pass
        else:
            d.ellipse((x-14*scale, y-17*scale, x+14*scale, y+18*scale), fill=(139, 132, 125, 255), outline=ink, width=3)
            d.polygon(((x-18*scale, y-13*scale), (x, y-34*scale), (x+18*scale, y-13*scale)), fill=(92, 72, 112, 255), outline=ink)
            d.ellipse((x-4*scale, y-3*scale, x+4*scale, y+5*scale), fill=(111, 175, 205, 230))
    elif kind in {"cave", "dungeon_entrance", "mine", "dragon_lair"}:
        d.arc((x-22*scale, y-15*scale, x+22*scale, y+20*scale), 180, 360, fill=(55, 50, 45, 255), width=max(4, int(7*scale)))
        d.ellipse((x-13*scale, y-1*scale, x+13*scale, y+14*scale), fill=(31, 30, 28, 255))
        if kind == "dragon_lair":
            d.polygon(((x-11*scale,y+2*scale),(x,y-9*scale),(x+11*scale,y+2*scale),(x+5*scale,y+11*scale),(x,y+5*scale),(x-5*scale,y+11*scale)),fill=(122,61,46,235),outline=ink)
    elif kind == "bandit_camp":
        d.polygon(((x-20*scale,y+13*scale),(x,y-18*scale),(x+20*scale,y+13*scale)),fill=(145,124,91,245),outline=ink)
        d.line((x-23*scale,y+16*scale,x+23*scale,y+16*scale),fill=(82,57,39,230),width=max(2,int(4*scale)))
    elif kind == "ruined_city":
        d.rectangle((x-18*scale,y-13*scale,x-4*scale,y+15*scale),fill=(124,116,105,220),outline=ink,width=2)
        d.rectangle((x+2*scale,y-7*scale,x+17*scale,y+15*scale),fill=(112,104,95,210),outline=ink,width=2)
        d.line((x-24*scale,y+16*scale,x+24*scale,y+16*scale),fill=ink,width=3)
    elif kind in {"ruin", "temple"}:
        d.rectangle((x-16*scale, y-14*scale, x-8*scale, y+15*scale), fill=(126, 123, 113, 240))
        d.rectangle((x+3*scale, y-20*scale, x+13*scale, y+13*scale), fill=(126, 123, 113, 240))
        d.line((x-22*scale, y+15*scale, x+22*scale, y+15*scale), fill=ink, width=3)
    else:
        d.ellipse((x-9*scale, y-9*scale, x+9*scale, y+9*scale), fill=(166, 126, 79, 240), outline=ink, width=2)
    if name:
        d.text((x, y+30*scale), name, anchor="ma", fill=(47, 37, 30, 255), stroke_width=2, stroke_fill=(235, 221, 185, 220))

def _color_to_biome(color: tuple[int,int,int])->str:
    best="plains"; best_d=float("inf")
    for name,c in BIOME_COLORS.items():
        d=sum((color[i]-c[i])**2 for i in range(3))
        if d<best_d: best_d=d; best=name
    return best


def _rect_overlap(a,b)->bool:
    return not (a[2]<b[0] or a[0]>b[2] or a[3]<b[1] or a[1]>b[3])


def _structure_name(kind:str,rng:random.Random)->str:
    a=["Old","Silver","Broken","Mossy","Sunken","Red","Moon","Iron","Whispering","Forgotten","High"]
    b={"house":"House","inn":"Inn","farm":"Farm","temple":"Temple","wizard_tower":"Wizard Tower","witch_hut":"Witch Hut","ruin":"Ruins","fort":"Fort","cave":"Cave","oasis":"Oasis","desert_temple":"Desert Temple","dock":"Dock","boat":"Boat","shipwreck":"Shipwreck","sea_ruin":"Sea Ruins","ocean_monument":"Ocean Monument","island":"Island","windmill":"Windmill"}
    noun=b.get(kind,kind.replace("_"," ").title())
    if kind in {"boat","shipwreck","cave","oasis","island"}: return f"{rng.choice(a)} {noun}"
    return f"{rng.choice(a)} {noun}"


def _building_name(kind:str,rng:random.Random)->str:
    names={
        "house":["Cottage","Town House","Home"],"tavern":["The Copper Mug","The Sleeping Griffin","The Lantern Inn"],
        "blacksmith":["Ironworks","Blacksmith","Forge"],"shop":["General Store","Market Shop","Provisioner"],
        "temple":["Temple","Chapel","Shrine"],"castle":["Keep","Castle","Citadel"],"farmhouse":["Farmhouse","Homestead"],
        "library":["Library","Archive"],"wizard_tower":["Wizard Tower","Arcane Spire"],
        "apothecary":["Apothecary","Herbalist"],"bakery":["Bakery","Bakehouse"],"stable":["Stable","Livery"],
        "guild_hall":["Guild Hall","Company Hall"],"warehouse":["Warehouse","Storehouse"],"government":["Council Hall","Magistrate"],
        "noble_house":["Noble House","Manor"],"mage_shop":["Arcane Shop","Enchanter"]}
    return rng.choice(names.get(kind,[kind.replace("_"," ").title()]))


def _normalize_building_type(kind:str)->str:
    mapping={"inn":"tavern","fort":"castle","farm":"farmhouse","windmill":"farmhouse","desert_temple":"temple","witch_hut":"wizard_tower","ocean_monument":"castle","rough_hut":"house","tent":"house","ruined_house":"house"}
    return mapping.get(kind,kind if kind in BUILDING_PROFILES else "house")


def _building_floor_count(kind: str, width: float, height: float, rng: random.Random) -> int:
    area = max(1.0, width * height)
    if kind in {"wizard_tower", "castle"}:
        return rng.randint(4, 6) if kind == "wizard_tower" else rng.randint(3, 5)
    if kind in {"government", "guild_hall", "noble_house", "tavern"}:
        return 2 if area < 5000 else rng.randint(2, 3)
    if kind in {"warehouse", "stable", "blacksmith", "shop", "apothecary", "bakery"}:
        return 1 if area < 5000 else 2
    if kind == "house":
        return 1 if area < 3400 else (2 if rng.random() < .55 else 1)
    return 1


def _paste_asset(image:Image.Image,asset:Image.Image|None,x:float,y:float,w:int,h:int)->bool:
    if asset is None:return False
    sprite=asset.copy()
    sprite.thumbnail((max(1,w),max(1,h)),Image.Resampling.LANCZOS)
    px=int(x-sprite.width/2); py=int(y-sprite.height/2)
    image.paste(sprite,(px,py),sprite)
    return True


def _draw_settlement_marker(image:Image.Image,x:float,y:float,kind:str,name:str)->None:
    d=ImageDraw.Draw(image,"RGBA")
    r=18 if kind=="village" else 24
    d.ellipse((x-r,y-r,x+r,y+r),fill=(222,204,151,220),outline=(65,49,36,255),width=3)
    d.rectangle((x-9,y-5,x+9,y+9),fill=(196,167,119,255),outline=(65,49,36,255),width=2)
    d.polygon(((x-12,y-5),(x,y-16),(x+12,y-5)),fill=(125,72,53,255),outline=(65,49,36,255))
    d.text((x,y+r+6),name,anchor="ma",fill=(46,36,29,255),stroke_width=2,stroke_fill=(235,221,185,220))


def _draw_building(image:Image.Image,x:float,y:float,kind:str,assets:AssetLibrary,scale:float=1.0)->None:
    slot={"house":"building.house","inn":"building.tavern","witch_hut":"building.house","temple":"building.temple"}.get(kind)
    if slot and _paste_asset(image,assets.get_slot(slot),x,y,int(54*scale),int(54*scale)):return
    d=ImageDraw.Draw(image,"RGBA"); ink=(65,48,36,255)
    if kind in {"house","inn","farm","windmill","witch_hut","dock"}:
        w=int((16 if kind!="inn" else 22)*scale);h=int((11 if kind!="inn" else 15)*scale)
        d.rectangle((x-w,y-h,x+w,y+h),fill=(205,183,139,255),outline=ink,width=2)
        d.polygon(((x-w-3,y-h),(x,y-h-int(12*scale)),(x+w+3,y-h)),fill=(116,68,49,255),outline=ink)
        if kind=="windmill":
            d.line((x,y-24,x,y+24),fill=ink,width=2);d.line((x-24,y,x+24,y),fill=ink,width=2)
        if kind=="dock":d.line((x-30,y+20,x+30,y+20),fill=(91,62,40,255),width=8)
    elif kind in {"temple","desert_temple"}:
        d.rectangle((x-18,y-15,x+18,y+15),fill=(194,188,165,255),outline=ink,width=3)
        d.polygon(((x-22,y-15),(x,y-31),(x+22,y-15)),fill=(118,108,88,255),outline=ink)
    elif kind in {"fort","ruin"}:
        fill=(139,136,127,255)
        d.rectangle((x-22,y-18,x+22,y+18),fill=fill,outline=ink,width=3)
        if kind=="ruin":d.rectangle((x-5,y-22,x+10,y-4),fill=BIOME_COLORS.get("plains")+(255,))
    elif kind in {"cave", "dungeon_entrance", "mine"}:
        d.arc((x-18,y-12,x+18,y+18),180,360,fill=(54,47,41,255),width=6);d.ellipse((x-11,y,x+11,y+12),fill=(35,32,30,255))
        if kind == "dungeon_entrance": d.rectangle((x-8,y+1,x+8,y+13),outline=(124,115,99,255),width=2)
        if kind == "mine": d.line((x-13,y+10,x+13,y-3),fill=(114,80,47,255),width=3)
    elif kind=="oasis":
        d.ellipse((x-24,y-12,x+24,y+12),fill=(65,139,164,255),outline=(48,98,112,255),width=2)
        _draw_tree(image,x-20,y-12,.8,assets);_draw_tree(image,x+20,y-10,.8,assets)
    else:
        d.ellipse((x-9,y-9,x+9,y+9),fill=(160,122,78,240),outline=ink,width=2)


def _draw_topdown_building(image:Image.Image,e:Entity,assets:AssetLibrary)->None:
    slot={"house":"building.house","tavern":"building.tavern","blacksmith":"building.blacksmith","temple":"building.temple","shop":"building.shop"}.get(e.subtype)
    if slot and _paste_asset(image,assets.get_slot(slot),e.x,e.y,int(e.width),int(e.height)):return
    d=ImageDraw.Draw(image,"RGBA")
    x1,y1,x2,y2=e.x-e.width/2,e.y-e.height/2,e.x+e.width/2,e.y+e.height/2
    walls=(199,180,143,255); roof=(119,70,53,245); ink=(60,45,34,255)
    if e.subtype in {"blacksmith","castle","temple"}: walls=(174,171,158,255)
    if e.subtype=="shop": roof=(104,82,57,245)
    d.rectangle((x1,y1,x2,y2),fill=walls,outline=ink,width=3)
    d.polygon(((x1-3,y1),(e.x,y1-e.height*.28),(x2+3,y1)),fill=roof,outline=ink)
    d.rectangle((e.x-4,y2-9,e.x+4,y2),fill=(86,57,39,255))


def _draw_tree(image,x,y,scale,assets):
    if _paste_asset(image,assets.get_slot("tree"),x,y,int(32*scale),int(42*scale)):return
    d=ImageDraw.Draw(image,"RGBA");s=max(2,int(4*scale));r=max(5,int(12*scale))
    d.rectangle((x-s/2,y,x+s/2,y+r),fill=(78,50,31,220));d.ellipse((x-r,y-r*1.4,x+r,y+r*.5),fill=(38,91,48,225),outline=(29,66,36,160))


def _draw_bush(image,x,y,scale):
    d=ImageDraw.Draw(image,"RGBA");r=max(3,int(7*scale));d.ellipse((x-r,y-r/2,x+r,y+r/2),fill=(52,103,52,190),outline=(40,75,40,130))


def _draw_cactus(image,x,y):
    d=ImageDraw.Draw(image,"RGBA");c=(62,118,66,230);d.line((x,y+8,x,y-10),fill=c,width=4);d.line((x,y-1,x-5,y-5),fill=c,width=3);d.line((x-5,y-5,x-5,y-9),fill=c,width=3);d.line((x,y+1,x+5,y-3),fill=c,width=3)


def _draw_rock(image,x,y,r,assets):
    if _paste_asset(image,assets.get_slot("rock"),x,y,r*3,r*2):return
    d=ImageDraw.Draw(image,"RGBA");d.polygon(((x-r,y+r/2),(x-r/2,y-r/2),(x,y-r),(x+r,y+r/2)),fill=(107,104,96,175),outline=(77,74,69,150))


def _draw_cart(image,x,y,assets):
    if _paste_asset(image,assets.get_slot("object.cart"),x,y,42,30):return
    d=ImageDraw.Draw(image,"RGBA");d.rectangle((x-16,y-9,x+16,y+9),fill=(111,76,45,245),outline=(62,44,31,255),width=2);d.ellipse((x-15,y+6,x-7,y+14),fill=(55,45,37,255));d.ellipse((x+7,y+6,x+15,y+14),fill=(55,45,37,255));d.line((x+16,y,x+28,y),fill=(73,50,34,255),width=3)


def _draw_sign(image,x,y):
    d=ImageDraw.Draw(image,"RGBA");d.line((x,y-2,x,y+18),fill=(77,52,35,255),width=3);d.rectangle((x-12,y-12,x+12,y),fill=(142,99,58,255),outline=(68,46,32,255))


def _draw_ocean_feature(image,x,y,kind,assets):
    d=ImageDraw.Draw(image,"RGBA")
    if kind in {"boat","shipwreck"}:
        if _paste_asset(image,assets.get_slot("object.boat"),x,y,56,38):return
        d.polygon(((x-24,y-6),(x+24,y-6),(x+15,y+10),(x-15,y+10)),fill=(104,70,43,245),outline=(58,42,31,255),width=2)
        d.line((x,y-7,x,y-28),fill=(69,51,38,255),width=3);d.polygon(((x+2,y-27),(x+2,y-8),(x+18,y-14)),fill=(224,212,178,210))
        if kind=="shipwreck":d.line((x-25,y+11,x+25,y-11),fill=(58,42,31,255),width=4)
    elif kind=="sea_ruin":
        for ox,oy in [(-18,-10),(0,4),(17,-7)]:d.rectangle((x+ox-6,y+oy-10,x+ox+6,y+oy+10),fill=(105,123,116,200),outline=(60,78,74,220),width=2)
    elif kind=="ocean_monument":
        d.polygon(((x-30,y+18),(x-22,y-10),(x,y-28),(x+22,y-10),(x+30,y+18)),fill=(102,143,132,235),outline=(52,83,78,255));d.rectangle((x-8,y-4,x+8,y+18),fill=(54,83,78,255))
    elif kind=="island":
        d.ellipse((x-36,y-22,x+36,y+22),fill=(208,191,131,255),outline=(157,141,91,255),width=2);d.ellipse((x-24,y-14,x+24,y+15),fill=(97,143,78,255));_draw_tree(image,x,y-8,.8,assets)


def _fill_floor(image:Image.Image,rect:tuple[int,int,int,int],kind:str,assets:AssetLibrary,seed:int)->None:
    x1,y1,x2,y2=rect
    slot="floor.stone" if kind=="stone" else "floor.wood"
    texture=assets.get_slot(slot)
    if texture:
        tile=texture.copy();tile.thumbnail((96,96),Image.Resampling.LANCZOS)
        if tile.width and tile.height:
            for y in range(y1,y2,tile.height):
                for x in range(x1,x2,tile.width):image.paste(tile,(x,y),tile)
            return
    rng=random.Random(seed);d=ImageDraw.Draw(image,"RGBA")
    if kind=="stone":
        cell=28
        for y in range(y1,y2,cell):
            for x in range(x1,x2,cell):
                shade=rng.randint(-9,9);c=146+shade
                d.rectangle((x,y,min(x2,x+cell),min(y2,y+cell)),fill=(c,c-3,c-8,255),outline=(79,75,70,90))
    else:
        plank=18
        for y in range(y1,y2,plank):
            shade=rng.randint(-9,9);d.rectangle((x1,y,x2,min(y2,y+plank)),fill=(170+shade,132+shade,87+shade,255));d.line((x1,y,x2,y),fill=(88,63,43,110),width=1)
            step=rng.randint(80,130)
            for x in range(x1+rng.randint(0,40),x2,step):d.line((x,y,x,min(y2,y+plank)),fill=(90,65,44,90),width=1)



def _furniture_asset_slot(subtype: str) -> str | None:
    return {
        "table": "furniture.table",
        "chair": "furniture.chair",
        "bed": "furniture.bed",
        "chest": "furniture.chest",
        "shelf": "furniture.shelf",
        "wardrobe": "furniture.wardrobe",
        "lamp": "furniture.lamp",
        "desk": "furniture.desk",
        "cabinet": "furniture.cabinet",
        "rug": "furniture.rug",
        "fireplace": "furniture.fireplace",
        "barrel": "object.barrel",
        "crate": "object.crate",
        "plant": "object.plant",
        "weapon_rack": "object.weapon_rack",
    }.get(subtype)


def _furniture_specs(
    rect: tuple[int, int, int, int],
    room_name: str,
    building_type: str,
    rng: random.Random,
    detail: bool = False,
) -> list[tuple[str, str, float, float, float, float, int]]:
    x1,y1,x2,y2=rect
    if x2-x1 < 50 or y2-y1 < 50:
        return []
    cx=(x1+x2)/2; cy=(y1+y2)/2
    name=room_name.lower()
    out: list[tuple[str,str,float,float,float,float,int]]=[]

    def add(kind: str, label: str, x: float, y: float, w: float, h: float, rotation: int | None = None) -> None:
        rot = rng.choice([0,90,180,270]) if rotation is None else rotation
        margin=max(w,h)/2+5
        x=max(x1+margin,min(x2-margin,x)); y=max(y1+margin,min(y2-margin,y))
        out.append((kind,label,x,y,w,h,rot))

    def table_set(x: float, y: float, scale: float=1.0) -> None:
        rot=rng.choice([0,90])
        tw,th=(58*scale,34*scale) if rot==0 else (34*scale,58*scale)
        add("table","Table",x,y,tw,th,rot)
        offsets=[(-tw/2-15,0),(tw/2+15,0)] if rot==0 else [(0,-th/2-15),(0,th/2+15)]
        if rng.random()<.7: offsets += ([(0,-th/2-15),(0,th/2+15)] if rot==0 else [(-tw/2-15,0),(tw/2+15,0)])
        for ox,oy in offsets:
            add("chair","Chair",x+ox,y+oy,20,20,(rot+90)%180)

    if any(k in name for k in ["living","common","great hall","meeting"]):
        table_set(cx+rng.uniform(-30,30),cy+rng.uniform(-20,20),1.0 if not detail else 1.15)
        if rng.random()<.8: add("rug","Rug",cx,cy+45,90,55,rng.choice([0,90]))
        if rng.random()<.65: add("fireplace","Fireplace",x1+30,cy,35,58,90)
        if rng.random()<.55: add("chest","Chest",x2-38,y2-30,36,24,0)
    elif any(k in name for k in ["bedroom","bedchamber","guest room"]):
        add("bed","Bed",x1+48,cy,52,78,rng.choice([0,180]))
        add("wardrobe","Wardrobe",x2-34,y1+38,40,58,rng.choice([0,90]))
        if rng.random()<.75: add("chest","Chest",x2-40,y2-32,38,24,0)
        if rng.random()<.6: add("lamp","Lamp",x1+82,y1+32,18,18,0)
    elif any(k in name for k in ["kitchen","pantry","bakery"]):
        add("cabinet","Cabinet",x1+30,cy,34,70,90)
        table_set(cx,cy,0.85)
        if rng.random()<.8: add("barrel","Barrel",x2-30,y2-30,28,28,0)
        if building_type in {"bakery","tavern"}: add("fireplace","Oven",x2-32,y1+38,38,52,90)
    elif any(k in name for k in ["storage","loading","tack"]):
        for _ in range(rng.randint(4,8 if detail else 6)):
            kind=rng.choice(["crate","barrel","chest"])
            add(kind,kind.title(),rng.uniform(x1+25,x2-25),rng.uniform(y1+25,y2-25),rng.choice([26,32,38]),rng.choice([24,28,34]))
    elif any(k in name for k in ["forge","workshop","armory","barracks"]):
        add("table","Workbench",cx,cy,70,32,rng.choice([0,90]))
        add("weapon_rack","Weapon Rack",x2-28,cy,30,70,90)
        if "barracks" in name:
            add("bed","Bunk",x1+40,y1+48,45,68,0); add("bed","Bunk",x1+40,y2-48,45,68,0)
        else:
            add("anvil","Anvil",x1+42,cy,36,30,rng.choice([0,90]))
            add("forge","Forge",x2-42,y1+42,54,46,0)
    elif any(k in name for k in ["library","stacks","archive","study","reading"]):
        shelf_count=max(2,min(6,int((x2-x1)/85)))
        for i in range(shelf_count):
            x=x1+28+i*(x2-x1-56)/max(1,shelf_count-1)
            add("shelf","Bookshelf",x,y1+30,26,60,0)
        add("desk","Desk",cx,y2-44,58,34,rng.choice([0,180]))
        add("chair","Chair",cx,y2-78,20,20,0)
    elif any(k in name for k in ["sanctuary","chapel"]):
        rows=max(2,min(5,int((y2-y1)/75)))
        for i in range(rows):
            add("bench","Pew",cx,y1+45+i*55,max(80,(x2-x1)*.64),20,0)
        add("altar","Altar",cx,y2-38,76,32,0)
    elif any(k in name for k in ["alchemy","ritual"]):
        table_set(cx,cy,.9)
        add("shelf","Potion Shelf",x2-26,cy,28,72,90)
        for _ in range(rng.randint(2,5)):
            add("plant","Herb Pot",rng.uniform(x1+25,x2-25),rng.uniform(y1+25,y2-25),18,18,0)
        if "ritual" in name: add("rug","Ritual Circle",cx,cy,96,96,0)
    elif any(k in name for k in ["sales","counter","shop"]):
        add("counter","Counter",cx,y1+36,max(85,(x2-x1)*.62),30,0)
        add("shelf","Shelf",x2-27,cy,28,max(70,(y2-y1)*.55),90)
        if building_type in {"apothecary","mage_shop"}:
            add("cabinet","Display Cabinet",x1+28,cy,30,70,90)
    elif "stable" in name:
        stalls=max(2,min(5,int((x2-x1)/75)))
        for i in range(stalls):
            add("stall","Horse Stall",x1+42+i*65,cy,48,80,0)
        add("barrel","Water Barrel",x2-28,y2-28,28,28,0)
    else:
        table_set(cx,cy,.85)
        if rng.random()<.55: add("cabinet","Cabinet",x2-28,y1+35,30,58,90)
        if rng.random()<.45: add("plant","Plant",x1+26,y2-28,20,20,0)

    if building_type == "wizard_tower" and rng.random()<.65:
        add("lamp","Arcane Lamp",x2-28,y2-28,20,20,0)
    if building_type == "tavern" and rng.random()<.7:
        add("barrel","Ale Barrel",x1+28,y2-28,30,30,0)
    if building_type == "blacksmith" and rng.random()<.7:
        add("crate","Metal Stock",x1+28,y2-28,34,28,0)
    if building_type == "temple" and rng.random()<.55:
        add("statue","Statue",x1+30,y1+34,28,45,0)

    if detail and (x2-x1)>180 and (y2-y1)>140 and rng.random()<.55:
        add("chess","Chess Board",x2-62,y2-55,42,42,rng.choice([0,90]))
    return out
