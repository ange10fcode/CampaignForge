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
    "mage_tower": 3,
    "street": 4,
    "building": 5,
    "room": 6,
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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "GenerationConfig":
        if not data:
            return cls()
        allowed = {k: bool(v) for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**allowed)


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
