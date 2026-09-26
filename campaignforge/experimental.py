from __future__ import annotations

import math
from typing import Iterable

from PIL import Image, ImageDraw

from .biomes import BIOME_COLORS
from .model import Entity, Scene


def _projector(scene: Scene, tilt_deg: float, rotation_deg: float, vertical_scale: float):
    w, h = scene.image.size
    cx, cy = w / 2.0, h / 2.0
    tilt = math.radians(max(0.0, min(72.0, tilt_deg)))
    rot = math.radians(rotation_deg % 360.0)
    cr, sr = math.cos(rot), math.sin(rot)
    squash = max(0.28, math.cos(tilt))
    lift = math.sin(tilt) * max(0.0, vertical_scale)
    y_shift = h * (0.08 + (1.0 - squash) * 0.20)

    def project(x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
        dx, dy = x - cx, y - cy
        rx = dx * cr - dy * sr
        ry = dx * sr + dy * cr
        px = cx + rx
        py = cy + ry * squash - z * lift + y_shift
        return px, py, ry

    return project


def _grid_value(grid, x: int, y: int, default):
    if not grid:
        return default
    y = max(0, min(len(grid) - 1, y))
    row = grid[y]
    if not row:
        return default
    x = max(0, min(len(row) - 1, x))
    return row[x]


def _entity_height(entity: Entity) -> float:
    kind = entity.subtype
    if entity.kind == "npc":
        if kind == "dragon" or entity.metadata.get("large_creature"):
            return max(26.0, entity.height * 0.72)
        return max(10.0, entity.height * 0.55)
    if kind in {"wizard_tower", "mage_tower"}:
        return max(65.0, entity.height * 1.4)
    if kind in {"castle", "fort"}:
        return max(48.0, entity.height * 0.9)
    if entity.kind in {"building", "settlement"}:
        floors = max(1, int(entity.metadata.get("floor_count", 1)))
        return max(20.0, 22.0 * floors)
    if entity.kind == "landmark":
        return max(10.0, entity.height * 0.45)
    return 8.0


def _entity_color(entity: Entity) -> tuple[int, int, int, int]:
    if entity.kind == "npc":
        if entity.subtype == "dragon":
            return (155, 66, 52, 255)
        if entity.subtype in {"guard", "townsperson"}:
            return (112, 145, 183, 255)
        return (196, 151, 92, 255)
    if entity.subtype in {"castle", "fort"}:
        return (142, 139, 132, 255)
    if entity.subtype in {"wizard_tower", "mage_tower", "mage_shop"}:
        return (118, 102, 139, 255)
    if entity.subtype in {"cave", "cave_mouth", "dragon_lair"}:
        return (72, 67, 61, 255)
    if entity.subtype in {"bandit_camp", "tent", "rough_hut"}:
        return (138, 111, 76, 255)
    if entity.subtype in {"ruin", "ruined_city", "ruined_house"}:
        return (126, 119, 107, 255)
    if entity.kind == "building":
        return (196, 174, 132, 255)
    return (160, 135, 96, 255)


def render_experimental(
    scene: Scene,
    tilt_deg: float = 48.0,
    rotation_deg: float = 28.0,
    vertical_scale: float = 1.0,
) -> Image.Image:
    """Render a lightweight 2.5D view from the same persistent scene data."""
    w, h = scene.image.size
    out = Image.new("RGB", (w, h), (23, 25, 29))
    draw = ImageDraw.Draw(out, "RGBA")
    project = _projector(scene, tilt_deg, rotation_deg, vertical_scale)

    biome_grid = scene.metadata.get("biome_grid") or [[scene.biome]]
    elevation_grid = scene.metadata.get("elevation_grid") or []
    rows = len(biome_grid)
    cols = len(biome_grid[0]) if rows else 1
    cols = max(1, cols)
    rows = max(1, rows)

    sx = max(1, math.ceil(cols / 64))
    sy = max(1, math.ceil(rows / 48))
    cell_w = w / cols
    cell_h = h / rows
    cells = []
    for gy in range(0, rows, sy):
        for gx in range(0, cols, sx):
            gx2 = min(cols, gx + sx)
            gy2 = min(rows, gy + sy)
            biome = _grid_value(biome_grid, gx, gy, scene.biome)
            elev = float(_grid_value(elevation_grid, gx, gy, 0.52)) if elevation_grid else 0.52
            z = max(0.0, (elev - 0.28) * 115.0)
            corners = [
                project(gx * cell_w, gy * cell_h, z),
                project(gx2 * cell_w, gy * cell_h, z),
                project(gx2 * cell_w, gy2 * cell_h, z),
                project(gx * cell_w, gy2 * cell_h, z),
            ]
            depth = sum(c[2] for c in corners) / 4.0
            color = BIOME_COLORS.get(biome, BIOME_COLORS.get("plains", (140, 160, 100)))
            shade = int(max(-24, min(18, (elev - 0.52) * 55)))
            fill = tuple(max(0, min(255, c + shade)) for c in color) + (255,)
            cells.append((depth, [(c[0], c[1]) for c in corners], fill))
    cells.sort(key=lambda item: item[0])
    for _, poly, fill in cells:
        draw.polygon(poly, fill=fill, outline=(30, 33, 36, 42))

    for path in scene.paths:
        pts = path.get("points", [])
        if len(pts) < 2:
            continue
        projected = [project(float(x), float(y), 3.0)[:2] for x, y in pts]
        kind = path.get("kind")
        if kind == "river":
            draw.line(projected, fill=(45, 117, 166, 255), width=max(3, w // 420), joint="curve")
        elif kind in {"road", "trail", "street"}:
            width = max(2, w // (340 if kind != "trail" else 500))
            draw.line(projected, fill=(104, 77, 52, 245), width=width + 2, joint="curve")
            draw.line(projected, fill=(162, 123, 77, 245), width=width, joint="curve")

    entities = []
    for entity in scene.entities:
        px, py, depth = project(entity.x, entity.y, 0.0)
        entities.append((depth, entity, px, py))
    entities.sort(key=lambda item: item[0])

    for _, entity, _, _ in entities:
        height = _entity_height(entity)
        base_w = max(8.0, entity.width * 0.68)
        base_h = max(7.0, entity.height * 0.50)
        x1, x2 = entity.x - base_w / 2, entity.x + base_w / 2
        y1, y2 = entity.y - base_h / 2, entity.y + base_h / 2
        base3 = [project(x1,y1,0),project(x2,y1,0),project(x2,y2,0),project(x1,y2,0)]
        top3 = [project(x1,y1,height),project(x2,y1,height),project(x2,y2,height),project(x1,y2,height)]
        base=[(p[0],p[1]) for p in base3]; top=[(p[0],p[1]) for p in top3]
        color=_entity_color(entity)
        side=(max(0,color[0]-38),max(0,color[1]-38),max(0,color[2]-38),245)
        side2=(max(0,color[0]-22),max(0,color[1]-22),max(0,color[2]-22),245)
        outline=(45,40,36,235)
        draw.polygon([base[0],base[1],top[1],top[0]],fill=side,outline=outline)
        draw.polygon([base[1],base[2],top[2],top[1]],fill=side2,outline=outline)
        draw.polygon(top,fill=color,outline=outline)
        if entity.kind == "npc":
            px,py,_=project(entity.x,entity.y,height+5)
            r=max(3,min(10,entity.width*.12))
            draw.ellipse((px-r,py-r,px+r,py+r),fill=(238,220,177,255),outline=(35,31,28,255),width=1)
        elif entity.name and entity.kind in {"settlement","landmark"}:
            px,py,_=project(entity.x,entity.y,height+8)
            draw.text((px,py-6),entity.name,anchor="ms",fill=(240,237,226,245),stroke_width=2,stroke_fill=(28,29,31,220))

    return out
