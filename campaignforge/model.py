from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from PIL import Image


SCENE_LEVELS = {
    "world": 0,
    "region": 1,
    "terrain": 2,
    "road": 2,
    "ocean": 2,
    "town": 3,
    "castle": 3,
    "cave": 3,
    "cavern": 4,
    "dragon_lair": 3,
    "bandit_camp": 3,
    "fort": 3,
    "mage_tower": 3,
    "street": 4,
    "building_section": 5,
    "building": 6,
    "floor": 6,
    "room": 7,
}


@dataclass
class GenerationConfig:
    towns: bool = True
    houses: bool = True
    roads: bool = True
    ruins: bool = True
    dungeons: bool = True
    castles: bool = True
    npcs: bool = True
    boats: bool = True
    carts: bool = True
    furniture: bool = True
    wildlife: bool = True
    decorations: bool = True
    ocean_structures: bool = True
    underground: bool = True

    temperature: int = 50
    max_altitude: int = 70
    moisture: int = 55
    road_min_per_settlement: int = 0
    road_max_per_settlement: int = 3
    road_connection_chance: int = 68
    rare_ruin_chance: int = 4
    snap_to_grid: bool = True
    experimental_smooth_terrain: bool = False

    def normalize(self) -> None:
        self.temperature = max(0, min(100, int(self.temperature)))
        self.max_altitude = max(0, min(100, int(self.max_altitude)))
        self.moisture = max(0, min(100, int(self.moisture)))
        self.road_min_per_settlement = max(0, min(8, int(self.road_min_per_settlement)))
        self.road_max_per_settlement = max(self.road_min_per_settlement, min(8, int(self.road_max_per_settlement)))
        self.road_connection_chance = max(0, min(100, int(self.road_connection_chance)))
        self.rare_ruin_chance = max(0, min(25, int(self.rare_ruin_chance)))
        self.snap_to_grid = bool(self.snap_to_grid)
        self.experimental_smooth_terrain = bool(self.experimental_smooth_terrain)

    def to_dict(self) -> dict:
        self.normalize()
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "GenerationConfig":
        cfg = cls()
        if not data:
            return cfg
        bool_fields = {
            "towns", "houses", "roads", "ruins", "dungeons", "castles", "npcs", "boats",
            "carts", "furniture", "wildlife", "decorations", "ocean_structures", "underground",
            "snap_to_grid", "experimental_smooth_terrain",
        }
        int_fields = {
            "temperature", "max_altitude", "moisture", "road_min_per_settlement", "road_max_per_settlement",
            "road_connection_chance", "rare_ruin_chance",
        }
        for key, value in data.items():
            if key not in cls.__dataclass_fields__:
                continue
            if key in bool_fields:
                setattr(cfg, key, bool(value))
            elif key in int_fields:
                try:
                    setattr(cfg, key, int(value))
                except (TypeError, ValueError):
                    pass
        cfg.normalize()
        return cfg


@dataclass
class Entity:
    id: str
    kind: str
    subtype: str
    name: str
    x: float
    y: float
    width: float = 24
    height: float = 24
    interactive: bool = True
    movable: bool = False
    child_scene_id: Optional[str] = None
    asset_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def contains(self, x: float, y: float, padding: float = 0) -> bool:
        return (
            self.x - self.width / 2 - padding <= x <= self.x + self.width / 2 + padding
            and self.y - self.height / 2 - padding <= y <= self.y + self.height / 2 + padding
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "subtype": self.subtype,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "interactive": self.interactive,
            "movable": self.movable,
            "child_scene_id": self.child_scene_id,
            "asset_id": self.asset_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Entity":
        return cls(**data)


@dataclass
class Scene:
    id: str
    title: str
    kind: str
    image: Image.Image
    seed: int
    parent_id: Optional[str] = None
    source_rect: Optional[tuple[int, int, int, int]] = None
    biome: str = "mixed"
    semantic: str = "region"
    entities: list[Entity] = field(default_factory=list)
    paths: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def level(self) -> int:
        return SCENE_LEVELS.get(self.kind, 1)

    def entity_at(self, x: float, y: float, padding: float = 8) -> Optional[Entity]:
        for entity in reversed(self.entities):
            if entity.interactive and entity.contains(x, y, padding):
                return entity
        return None

    def children_rects(self, scenes: dict[str, "Scene"]) -> list[tuple[tuple[int, int, int, int], str]]:
        out = []
        for scene in scenes.values():
            if scene.parent_id == self.id and scene.source_rect:
                out.append((scene.source_rect, scene.id))
        return out

    def sample_biome(self, x: float, y: float) -> str:
        grid = self.metadata.get("biome_grid")
        if not grid:
            return self.biome
        rows = len(grid)
        cols = len(grid[0]) if rows else 0
        if not cols:
            return self.biome
        gx = min(cols - 1, max(0, int(x / max(1, self.image.width) * cols)))
        gy = min(rows - 1, max(0, int(y / max(1, self.image.height) * rows)))
        return grid[gy][gx]

    def to_manifest(self, image_name: str) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "seed": self.seed,
            "parent_id": self.parent_id,
            "source_rect": list(self.source_rect) if self.source_rect else None,
            "biome": self.biome,
            "semantic": self.semantic,
            "entities": [entity.to_dict() for entity in self.entities],
            "paths": self.paths,
            "metadata": self.metadata,
            "image": image_name,
        }


@dataclass
class CampaignState:
    scenes: dict[str, Scene] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    current_id: Optional[str] = None
    config: GenerationConfig = field(default_factory=GenerationConfig)
    title: str = "Untitled Campaign"
    view_state: dict = field(default_factory=dict)

    def add_scene(self, scene: Scene) -> None:
        self.scenes[scene.id] = scene
        if scene.id not in self.order:
            self.order.append(scene.id)
        self.current_id = scene.id

    def current(self) -> Optional[Scene]:
        return self.scenes.get(self.current_id) if self.current_id else None

    def child_for_entity(self, entity: Entity) -> Optional[Scene]:
        if entity.child_scene_id:
            return self.scenes.get(entity.child_scene_id)
        return None

    def descendants(self, scene_id: str) -> list[str]:
        found: list[str] = []
        pending = [scene_id]
        while pending:
            parent = pending.pop()
            children = [s.id for s in self.scenes.values() if s.parent_id == parent]
            found.extend(children)
            pending.extend(children)
        return found

    def remove_scene(self, scene_id: str, include_descendants: bool = True) -> list[str]:
        scene = self.scenes.get(scene_id)
        if not scene or scene.parent_id is None:
            return []
        to_remove = [scene_id]
        if include_descendants:
            to_remove.extend(self.descendants(scene_id))
        remove_set = set(to_remove)
        parent_id = scene.parent_id
        for other in self.scenes.values():
            if other.id in remove_set:
                continue
            for entity in other.entities:
                if entity.child_scene_id in remove_set:
                    entity.child_scene_id = None
        for sid in to_remove:
            self.scenes.pop(sid, None)
        self.order = [sid for sid in self.order if sid not in remove_set]
        if self.current_id in remove_set:
            self.current_id = parent_id if parent_id in self.scenes else (self.order[0] if self.order else None)
        return to_remove
