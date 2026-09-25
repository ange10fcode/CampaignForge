from __future__ import annotations

import math
import random
import uuid
from collections import Counter
from dataclasses import dataclass
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
            settings = MapSettings(settings.width, settings.height, settings.seed, settings.detail, settings.river_count, 0)
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
                color = e._terrain_color(e.elevation[row][col], e.moisture[row][col])
                out.append(_color_to_biome(color))
            grid.append(out)
        self.metadata["biome_grid"] = grid
        self.metadata["biome_grid_size"] = [e.cols, e.rows]
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
                width=48 if settlement.kind == "village" else (68 if settlement.kind == "castle" else 58),
                height=48 if settlement.kind == "village" else (68 if settlement.kind == "castle" else 58),
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
            if landmark.kind in {"cave", "dungeon_entrance", "mine"} and not (self.config.underground or self.config.dungeons):
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
        for landmark in [e for e in self.entities if e.kind == "landmark" and e.subtype in {"wizard_tower", "cave"}]:
            if not anchors:
                break
            target = min(anchors, key=lambda e: math.hypot(e.x - landmark.x, e.y - landmark.y))
            distance = math.hypot(target.x - landmark.x, target.y - landmark.y)
            if landmark.subtype == "cave" and distance > min(self.settings.width, self.settings.height) * 0.34:
                continue
            steps = max(5, int(distance / 30))
            points = []
            for i in range(steps + 1):
                t = i / steps
                bend = math.sin(math.pi * t) * self.engine.rng.uniform(-18, 18)
                dx, dy = target.x - landmark.x, target.y - landmark.y
                ln = max(1.0, math.hypot(dx, dy))
                nx, ny = -dy / ln, dx / ln
                points.append((landmark.x + dx * t + nx * bend, landmark.y + dy * t + ny * bend))
            kind = "road" if landmark.subtype == "wizard_tower" else "trail"
            width = 3 if kind == "road" else 2
            draw.line(points, fill=(121, 91, 60, 175), width=width, joint="curve")
            self.paths.append({
                "id": str(uuid.uuid4()),
                "kind": kind,
                "major": landmark.subtype == "wizard_tower",
                "from_ref": landmark.metadata.get("world_ref"),
                "to_ref": target.metadata.get("world_ref"),
                "points": [[round(x, 2), round(y, 2)] for x, y in points],
            })

    def _sample_engine_biome(self, x: float, y: float) -> str:
        col = max(0, min(self.engine.cols - 1, int(x / self.engine.tile)))
        row = max(0, min(self.engine.rows - 1, int(y / self.engine.tile)))
        color = self.engine._terrain_color(self.engine.elevation[row][col], self.engine.moisture[row][col])
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
            elif clone.subtype in {"castle", "fort", "cave", "wizard_tower", "mage_tower", "ruin", "temple", "dungeon_entrance", "mine"}:
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
            landmark_types = {"ruin", "cave", "shipwreck", "sea_ruin", "ocean_monument", "dungeon_entrance", "mine", "wizard_tower", "fort"}
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
            elif kind in {"wizard_tower", "fort", "cave"}:
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
            if e.metadata.get("generated_local") and e.subtype in {"fort", "castle", "wizard_tower", "cave"}
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
            if loc.subtype == "cave" and distance > min(self.settings.width, self.settings.height) * .38:
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
        entrances = list(self.road_entrances)
        if not entrances:
            count = 2 if self.settlement.subtype == "village" else 3
            base = self.rng.uniform(-math.pi, math.pi)
            entrances = [base + i * math.tau / count + self.rng.uniform(-.18, .18) for i in range(count)]
        entrances = _dedupe_angles_local(entrances)[:5]
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
        target = max(20 if self.settlement.subtype == "village" else 28, base)
        used: list[tuple[float, float, float, float]] = []
        candidates: list[tuple[float, float]] = []
        for path in self.road_paths:
            for idx in range(3, len(path) - 3, 2):
                a = path[max(0, idx - 1)]
                b = path[min(len(path) - 1, idx + 1)]
                dx, dy = b[0] - a[0], b[1] - a[1]
                ln = max(1.0, math.hypot(dx, dy))
                nx, ny = -dy / ln, dx / ln
                for side in (-1, 1):
                    offset = self.rng.uniform(34, 52)
                    candidates.append((path[idx][0] + nx * offset * side, path[idx][1] + ny * offset * side))
        self.rng.shuffle(candidates)
        special = ["tavern", "blacksmith", "shop", "temple", "library"]
        if self.settlement.subtype == "castle" and self.config.castles:
            special.insert(0, "castle")
        placed = 0
        for x, y in candidates:
            if placed >= target:
                break
            btype = special[placed] if placed < len(special) else self.rng.choices(
                ["house", "house", "house", "shop", "farmhouse"], weights=[6, 6, 6, 1, 1]
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
                metadata={"biome": self.biome, "building_type": btype, "town": self.settlement.name},
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
            if any(math.hypot(x-px, y-py) < 22 for path in self.road_paths for px, py in path[::3]):
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
        choices=[]
        if self.biome in {"forest","dark_forest"}:
            if self.config.houses: choices += ["witch_hut","house"]
            if self.config.ruins: choices += ["ruin","cave"]
        elif self.biome in {"desert","dry_plains"}:
            if self.config.ruins: choices += ["desert_temple","ruin"]
            choices += ["oasis"]
        elif self.biome in {"mountain","hills","snow"}:
            if self.config.ruins: choices += ["cave","ruin"]
            if self.config.castles: choices += ["fort"]
            if self.config.dungeons: choices += ["dungeon_entrance"]
            if self.config.underground: choices += ["mine"]
        else:
            if self.config.houses: choices += ["farm","house"]
            if self.config.ruins: choices += ["ruin"]
            if self.config.dungeons: choices += ["dungeon_entrance"]
        for i in range(min(self.settings.landmark_count,len(choices)+3)):
            kind=self.rng.choice(choices) if choices else None
            if not kind or not compatible(kind,self.biome): continue
            x,y=self.rng.randint(60,self.settings.width-60),self.rng.randint(60,self.settings.height-60)
            _draw_building(self.image,x,y,kind,self.assets,.9)
            self.entities.append(Entity(str(uuid.uuid4()),"building" if kind not in {"ruin","cave","dungeon_entrance","mine"} else "landmark",kind,_structure_name(kind,self.rng),x,y,44,44,metadata={"biome":self.biome,"building_type":_normalize_building_type(kind)}))
            self._progress("Adding biome-valid structures",.65+.25*(i+1)/max(1,self.settings.landmark_count))
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
        keep = Entity(str(uuid.uuid4()), "building", "castle", f"{self.location.name} Keep", cx, cy-35, 150, 105, metadata={"biome": self.biome, "building_type": "castle", "location": self.location.name})
        support = [
            Entity(str(uuid.uuid4()), "building", "blacksmith", "Castle Forge", cx-wall_w*.25, cy+wall_h*.20, 70, 48, metadata={"biome": self.biome, "building_type": "blacksmith"}),
            Entity(str(uuid.uuid4()), "building", "house", "Barracks", cx+wall_w*.24, cy+wall_h*.20, 92, 52, metadata={"biome": self.biome, "building_type": "house"}),
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

class BuildingInteriorGenerator(SceneGenerator):
    def __init__(self, building: Entity, width: int, height: int, seed: int, config: GenerationConfig, assets: AssetLibrary, status) -> None:
        super().__init__(status)
        self.building=building
        self.width=width
        self.height=height
        self.seed=seed
        self.config=config
        self.assets=assets
        self.rng=random.Random(seed)
        self.biome=building.metadata.get("biome","plains")
        self.semantic="building"
        self.building_type=_normalize_building_type(building.metadata.get("building_type") or building.subtype)
        self.profile=BUILDING_PROFILES.get(self.building_type,BUILDING_PROFILES["house"])
        self.image=make_biome_canvas(width,height,self.biome,seed+40,.8)
        self.draw=ImageDraw.Draw(self.image,"RGBA")
        self.room_rects:list[tuple[int,int,int,int,str]]=[]

    def generate(self)->Generator[None,None,None]:
        yield from self._shell_and_rooms()
        yield from self._furnish()
        self.metadata["biome_grid"]=[[self.biome for _ in range(40)] for _ in range(30)]
        self.metadata["building_type"]=self.building_type
        self._progress("Building interior complete",1.0)
        yield

    def _shell_and_rooms(self)->Generator[None,None,None]:
        margin=max(65,min(self.width,self.height)//10)
        x1,y1,x2,y2=margin,margin,self.width-margin,self.height-margin
        wall=self.profile["wall"]
        floor_kind=self.profile["floor"]
        _fill_floor(self.image,(x1,y1,x2,y2),floor_kind,self.assets,self.seed)
        self.draw.rectangle((x1,y1,x2,y2),outline=wall+(255,),width=14)
        names=self.profile["rooms"][:]
        room_count=max(2,min(len(names),6))
        cols=2 if room_count<=4 else 3
        rows=math.ceil(room_count/cols)
        cell_w=(x2-x1)//cols
        cell_h=(y2-y1)//rows
        for i in range(room_count):
            col=i%cols; row=i//cols
            rx1=x1+col*cell_w
            ry1=y1+row*cell_h
            rx2=x2 if col==cols-1 else x1+(col+1)*cell_w
            ry2=y2 if row==rows-1 else y1+(row+1)*cell_h
            name=names[i]
            self.room_rects.append((rx1,ry1,rx2,ry2,name))
            if col>0:
                self.draw.line((rx1,ry1,rx1,ry2),fill=wall+(255,),width=9)
            if row>0:
                self.draw.line((rx1,ry1,rx2,ry1),fill=wall+(255,),width=9)
            cx=(rx1+rx2)//2; cy=(ry1+ry2)//2
            self.entities.append(Entity(str(uuid.uuid4()),"room",name.replace(" ","_"),name.title(),cx,cy,rx2-rx1-12,ry2-ry1-12,metadata={"bounds":[rx1,ry1,rx2,ry2],"building_type":self.building_type,"biome":self.biome,"floor":floor_kind}))
            self._progress("Creating connected rooms",.08+.30*(i+1)/room_count)
            yield
        for i,room in enumerate(self.room_rects):
            rx1,ry1,rx2,ry2,_=room
            if i%cols!=cols-1 and i+1<room_count:
                y=(ry1+ry2)//2
                self.draw.rectangle((rx2-5,y-14,rx2+5,y+14),fill=(185,133,75,255))
            if i+cols<room_count:
                x=(rx1+rx2)//2
                self.draw.rectangle((x-14,ry2-5,x+14,ry2+5),fill=(185,133,75,255))
        entrance_x=(x1+x2)//2
        self.draw.rectangle((entrance_x-18,y2-8,entrance_x+18,y2+8),fill=(178,124,69,255))
        yield

    def _furnish(self)->Generator[None,None,None]:
        if not self.config.furniture:
            return
        for i,(x1,y1,x2,y2,name) in enumerate(self.room_rects):
            _furnish_room(self.image,(x1+14,y1+14,x2-14,y2-14),name,self.building_type,self.rng,self.assets)
            self._progress(f"Furnishing {name}",.45+.48*(i+1)/len(self.room_rects))
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
            _furnish_room(self.image,(35,35,self.width-35,self.height-35),self.room.name.lower(),self.room.metadata.get("building_type","house"),self.rng,self.assets,detail=True)
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
    elif kind in {"cave", "dungeon_entrance", "mine"}:
        d.arc((x-22*scale, y-15*scale, x+22*scale, y+20*scale), 180, 360, fill=(55, 50, 45, 255), width=max(4, int(7*scale)))
        d.ellipse((x-13*scale, y-1*scale, x+13*scale, y+14*scale), fill=(31, 30, 28, 255))
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
        "library":["Library","Archive"],"wizard_tower":["Wizard Tower","Arcane Spire"]}
    return rng.choice(names.get(kind,[kind.replace("_"," ").title()]))


def _normalize_building_type(kind:str)->str:
    mapping={"inn":"tavern","fort":"castle","farm":"farmhouse","windmill":"farmhouse","desert_temple":"temple","witch_hut":"wizard_tower","ocean_monument":"castle"}
    return mapping.get(kind,kind if kind in BUILDING_PROFILES else "house")


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


def _furnish_room(image:Image.Image,rect:tuple[int,int,int,int],room_name:str,building_type:str,rng:random.Random,assets:AssetLibrary,detail:bool=False)->None:
    x1,y1,x2,y2=rect;d=ImageDraw.Draw(image,"RGBA");w=x2-x1;h=y2-y1
    def table(x,y,scale=1):
        asset=assets.get_slot("furniture.table")
        if _paste_asset(image,asset,x,y,int(50*scale),int(34*scale)):return
        d.rectangle((x-20*scale,y-10*scale,x+20*scale,y+10*scale),fill=(105,73,45,255),outline=(57,43,33,255),width=2)
    def chair(x,y):
        asset=assets.get_slot("furniture.chair")
        if _paste_asset(image,asset,x,y,20,20):return
        d.rectangle((x-5,y-5,x+5,y+5),fill=(84,61,43,255))
    def bed(x,y):
        asset=assets.get_slot("furniture.bed")
        if _paste_asset(image,asset,x,y,52,78):return
        d.rectangle((x-22,y-34,x+22,y+34),fill=(117,83,59,255),outline=(55,43,35,255),width=2);d.rectangle((x-18,y-29,x+18,y-17),fill=(205,197,170,255));d.rectangle((x-18,y-14,x+18,y+29),fill=(154,116,105,255))
    cx=(x1+x2)//2;cy=(y1+y2)//2;name=room_name.lower()
    if any(k in name for k in ["common","living","sales","great hall"]):
        for ox,oy in [(-w*.22,-h*.12),(w*.18,h*.12)]:
            table(cx+ox,cy+oy);chair(cx+ox-28,cy+oy);chair(cx+ox+28,cy+oy)
        if "sales" in name:d.rectangle((x2-35,y1+12,x2-18,y2-12),fill=(98,69,46,255))
    elif "bar" in name:
        d.rectangle((x1+18,y1+18,x2-18,y1+40),fill=(101,69,44,255));
        for x in range(x1+28,x2-25,26):chair(x,y1+55)
        for x in range(x1+22,x2-20,24):d.ellipse((x-7,y2-24,x+7,y2-10),fill=(117,75,42,255),outline=(61,45,34,255))
    elif any(k in name for k in ["bedroom","bedchamber","guest room"]):
        bed(x1+45,cy);d.rectangle((x2-45,y1+25,x2-15,y1+50),fill=(104,74,47,255));d.rectangle((x2-45,y2-50,x2-15,y2-20),fill=(116,82,48,255))
    elif any(k in name for k in ["kitchen","pantry"]):
        d.rectangle((x1+15,y1+15,x1+45,y2-15),fill=(108,78,50,255));d.ellipse((x2-58,cy-22,x2-18,cy+18),fill=(83,77,67,255),outline=(49,45,41,255),width=3);table(cx,cy)
    elif "forge" in name or "workshop" in name:
        d.rectangle((x1+18,cy-18,x1+68,cy+18),fill=(83,77,68,255),outline=(50,47,43,255),width=3);d.polygon(((cx-12,cy+8),(cx+12,cy+8),(cx+6,cy-4),(cx-6,cy-4)),fill=(69,68,66,255));d.rectangle((x2-45,y1+16,x2-18,y2-16),fill=(102,72,44,255))
    elif any(k in name for k in ["library","stacks","archive","study","reading"]):
        for x in range(x1+18,x2-20,36):d.rectangle((x,y1+12,x+18,y2-12),fill=(92,65,42,255));table(cx,cy);chair(cx,cy+26)
    elif any(k in name for k in ["sanctuary","chapel"]):
        for y in range(y1+28,y2-45,36):d.rectangle((x1+35,y,x2-35,y+10),fill=(103,76,51,255));d.rectangle((cx-30,y1+12,cx+30,y1+30),fill=(177,164,132,255))
    elif any(k in name for k in ["alchemy","ritual"]):
        table(cx,cy)
        for _ in range(8):
            x=rng.randint(x1+20,x2-20);y=rng.randint(y1+20,y2-20);d.ellipse((x-5,y-5,x+5,y+5),fill=rng.choice([(92,142,111,230),(145,92,159,230),(177,130,62,230)]),outline=(53,45,39,255))
        if "ritual" in name:d.ellipse((cx-45,cy-45,cx+45,cy+45),outline=(119,76,131,190),width=3)
    elif "storage" in name:
        for _ in range(7 if detail else 4):
            x=rng.randint(x1+18,x2-18);y=rng.randint(y1+18,y2-18);d.rectangle((x-11,y-8,x+11,y+8),fill=(113,78,44,255),outline=(62,44,32,255))
    else:
        table(cx,cy);chair(cx-30,cy);chair(cx+30,cy)
    if detail and w>180 and h>140:
        bx=x2-70;by=y2-60
        d.rectangle((bx-22,by-22,bx+22,by+22),fill=(122,86,52,255),outline=(57,43,33,255),width=2)
        for i in range(1,8):
            p=bx-22+i*44/8;d.line((p,by-22,p,by+22),fill=(55,45,38,100),width=1);d.line((bx-22,by-22+i*44/8,bx+22,by-22+i*44/8),fill=(55,45,38,100),width=1)
        d.text((bx,by),"♟",anchor="mm",fill=(42,36,32,255))
