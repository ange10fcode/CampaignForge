from __future__ import annotations

import math
import random
from collections import Counter

from PIL import Image, ImageDraw


BIOME_COLORS = {
    "deep_ocean": (48, 99, 132),
    "ocean": (73, 132, 163),
    "coast": (210, 194, 137),
    "plains": (146, 173, 105),
    "dry_plains": (173, 168, 105),
    "forest": (73, 119, 74),
    "dark_forest": (47, 89, 60),
    "hills": (133, 137, 87),
    "mountain": (111, 105, 94),
    "snow": (220, 221, 211),
    "desert": (204, 183, 111),
}

LAND_BIOMES = {"coast", "plains", "dry_plains", "forest", "dark_forest", "hills", "mountain", "snow", "desert"}
WATER_BIOMES = {"ocean", "deep_ocean"}

STRUCTURE_BIOMES = {
    "house": LAND_BIOMES - {"mountain", "snow"},
    "farm": {"plains", "dry_plains"},
    "windmill": {"plains", "dry_plains"},
    "inn": LAND_BIOMES - {"mountain", "snow"},
    "temple": LAND_BIOMES,
    "wizard_tower": LAND_BIOMES - {"ocean", "deep_ocean"},
    "witch_hut": {"forest", "dark_forest", "swamp"},
    "ruin": LAND_BIOMES,
    "fort": {"plains", "dry_plains", "hills", "mountain", "coast"},
    "cave": {"hills", "mountain", "forest", "dark_forest"},
    "dungeon_entrance": {"plains", "dry_plains", "forest", "dark_forest", "hills", "mountain", "desert"},
    "mine": {"hills", "mountain", "forest", "dark_forest"},
    "oasis": {"desert", "dry_plains"},
    "desert_temple": {"desert", "dry_plains"},
    "dock": {"coast"},
    "boat": WATER_BIOMES | {"coast"},
    "shipwreck": WATER_BIOMES | {"coast"},
    "sea_ruin": WATER_BIOMES,
    "ocean_monument": {"deep_ocean", "ocean"},
    "island": WATER_BIOMES,
}


def nearest_biome(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb[:3]
    best = "plains"
    best_d = float("inf")
    for name, color in BIOME_COLORS.items():
        d = (r - color[0]) ** 2 + (g - color[1]) ** 2 + (b - color[2]) ** 2
        if d < best_d:
            best_d = d
            best = name
    return best


def dominant_biome(scene, rect: tuple[int, int, int, int], samples: int = 11) -> str:
    x1, y1, x2, y2 = rect
    found = []
    for sy in range(samples):
        y = y1 + (y2 - y1) * (sy + .5) / samples
        for sx in range(samples):
            x = x1 + (x2 - x1) * (sx + .5) / samples
            found.append(scene.sample_biome(x, y))
    return Counter(found).most_common(1)[0][0] if found else scene.biome


def compatible(kind: str, biome: str) -> bool:
    allowed = STRUCTURE_BIOMES.get(kind)
    return biome in allowed if allowed else True


def biome_base_color(biome: str) -> tuple[int, int, int]:
    return BIOME_COLORS.get(biome, BIOME_COLORS["plains"])


def draw_biome_texture(image: Image.Image, biome: str, seed: int, density: float = 1.0) -> None:
    rng = random.Random(seed)
    d = ImageDraw.Draw(image, "RGBA")
    w, h = image.size
    count = max(100, int(w * h / 2600 * density))
    if biome in WATER_BIOMES:
        for _ in range(count):
            x = rng.randrange(0, w)
            y = rng.randrange(0, h)
            span = rng.randint(8, 30)
            d.arc((x - span, y - 3, x + span, y + 5), 195, 345, fill=(225, 247, 249, rng.randint(35, 95)), width=1)
        return
    if biome in {"forest", "dark_forest"}:
        for _ in range(count):
            x, y = rng.randrange(w), rng.randrange(h)
            r = rng.choice((1, 1, 2, 3))
            d.ellipse((x-r, y-r, x+r, y+r), fill=(27, 66, 33, rng.randint(20, 80)))
    elif biome in {"desert", "dry_plains", "coast"}:
        for _ in range(count):
            x, y = rng.randrange(w), rng.randrange(h)
            d.ellipse((x, y, x+2, y+1), fill=(112, 94, 59, rng.randint(20, 70)))
    elif biome in {"mountain", "snow", "hills"}:
        for _ in range(count):
            x, y = rng.randrange(w), rng.randrange(h)
            span = rng.randint(2, 7)
            d.line((x-span, y+span, x, y-span, x+span, y+span), fill=(65, 60, 55, rng.randint(25, 80)), width=1)
    else:
        for _ in range(count):
            x, y = rng.randrange(w), rng.randrange(h)
            d.line((x, y+2, x+rng.choice((-1, 0, 1)), y-2), fill=(66, 100, 47, rng.randint(25, 75)), width=1)


def make_biome_canvas(width: int, height: int, biome: str, seed: int, density: float = 1.0) -> Image.Image:
    base = biome_base_color(biome)
    image = Image.new("RGB", (width, height), base)
    draw_biome_texture(image, biome, seed, density)
    return image


def rect_aspect_dimensions(rect: tuple[int, int, int, int], max_width: int, max_height: int, minimum: int = 500) -> tuple[int, int]:
    x1, y1, x2, y2 = rect
    rw = max(1, x2 - x1)
    rh = max(1, y2 - y1)
    ratio = rw / rh
    width = max_width
    height = int(round(width / ratio))
    if height > max_height:
        height = max_height
        width = int(round(height * ratio))
    if width < minimum:
        width = minimum
        height = int(round(width / ratio))
    if height < minimum:
        height = minimum
        width = int(round(height * ratio))
    return max(320, width), max(320, height)


def point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    x = ax + t * dx
    y = ay + t * dy
    return math.hypot(px - x, py - y)
