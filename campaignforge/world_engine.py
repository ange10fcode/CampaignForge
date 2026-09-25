from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Generator, Iterable, Optional

try:
    from PIL import Image, ImageDraw
except ImportError as exc:
    raise SystemExit(
        "Pillow is required.\nInstall it with:\n\n"
        "    py -m pip install pillow\n"
    ) from exc


Color = tuple[int, int, int]
Point = tuple[float, float]


@dataclass
class MapSettings:
    width: int
    height: int
    seed: int
    detail: int
    river_count: int
    settlement_count: int


@dataclass
class Settlement:
    x: int
    y: int
    kind: str
    name: str


@dataclass
class Landmark:
    x: int
    y: int
    kind: str
    name: str


class Noise2D:

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def _hash(self, x: int, y: int) -> float:
        n = x * 374761393 + y * 668265263 + self.seed * 69069
        n = (n ^ (n >> 13)) * 1274126177
        n ^= n >> 16
        return (n & 0xFFFFFFFF) / 0xFFFFFFFF

    @staticmethod
    def _smooth(t: float) -> float:
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def _lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    def value(self, x: float, y: float) -> float:
        x0 = math.floor(x)
        y0 = math.floor(y)
        tx = self._smooth(x - x0)
        ty = self._smooth(y - y0)

        a = self._hash(x0, y0)
        b = self._hash(x0 + 1, y0)
        c = self._hash(x0, y0 + 1)
        d = self._hash(x0 + 1, y0 + 1)

        top = self._lerp(a, b, tx)
        bottom = self._lerp(c, d, tx)
        return self._lerp(top, bottom, ty)

    def fractal(
        self,
        x: float,
        y: float,
        octaves: int = 5,
        persistence: float = 0.5,
        lacunarity: float = 2.0,
    ) -> float:
        total = 0.0
        amplitude = 1.0
        frequency = 1.0
        max_value = 0.0

        for _ in range(octaves):
            total += self.value(x * frequency, y * frequency) * amplitude
            max_value += amplitude
            amplitude *= persistence
            frequency *= lacunarity

        return total / max_value if max_value else 0.0


class FantasyMapGenerator:
    WATER = (73, 132, 163)
    DEEP_WATER = (48, 99, 132)
    BEACH = (210, 194, 137)
    PLAINS = (146, 173, 105)
    DRY_PLAINS = (173, 168, 105)
    FOREST = (73, 119, 74)
    DARK_FOREST = (47, 89, 60)
    HILLS = (133, 137, 87)
    MOUNTAIN = (111, 105, 94)
    SNOW = (220, 221, 211)
    RIVER = (45, 117, 166)
    ROAD = (135, 104, 72)
    INK = (67, 54, 42)

    def __init__(
        self,
        settings: MapSettings,
        status_callback: Callable[[str, float], None],
    ) -> None:
        self.settings = settings
        self.status_callback = status_callback
        self.rng = random.Random(settings.seed)
        self.noise = Noise2D(settings.seed)

        self.image = Image.new("RGB", (settings.width, settings.height), self.PLAINS)
        self.draw = ImageDraw.Draw(self.image, "RGBA")

        self.elevation: list[list[float]] = []
        self.moisture: list[list[float]] = []
        self.land_mask: list[list[bool]] = []
        self.mountain_points: list[Point] = []
        self.settlements: list[Settlement] = []
        self.river_paths: list[list[Point]] = []
        self.road_paths: list[list[Point]] = []
        self.landmarks: list[Landmark] = []

        self.tile = max(4, int(18 - settings.detail * 1.5))
        self.cols = math.ceil(settings.width / self.tile)
        self.rows = math.ceil(settings.height / self.tile)

    def _progress(self, message: str, value: float) -> None:
        self.status_callback(message, max(0.0, min(1.0, value)))

    def _radial_island_bias(self, x: float, y: float) -> float:
        nx = (x / self.settings.width) * 2.0 - 1.0
        ny = (y / self.settings.height) * 2.0 - 1.0
        d = math.sqrt(nx * nx + ny * ny)
        edge = max(0.0, 1.0 - d ** 1.7)
        return edge

    def _sample_grid(self, grid: list[list[float]], x: float, y: float) -> float:
        gx = max(0, min(self.cols - 1, int(x / self.tile)))
        gy = max(0, min(self.rows - 1, int(y / self.tile)))
        return grid[gy][gx]

    def _is_land(self, x: float, y: float) -> bool:
        gx = max(0, min(self.cols - 1, int(x / self.tile)))
        gy = max(0, min(self.rows - 1, int(y / self.tile)))
        return self.land_mask[gy][gx]

    def _terrain_color(self, elev: float, moist: float) -> Color:
        if elev < 0.34:
            return self.DEEP_WATER
        if elev < 0.40:
            return self.WATER
        if elev < 0.44:
            return self.BEACH
        if elev > 0.78:
            return self.SNOW
        if elev > 0.68:
            return self.MOUNTAIN
        if elev > 0.58:
            return self.HILLS
        if moist > 0.67:
            return self.DARK_FOREST
        if moist > 0.53:
            return self.FOREST
        if moist < 0.32:
            return self.DRY_PLAINS
        return self.PLAINS

    def generate(self) -> Generator[None, None, None]:
        yield from self._stage_base_noise()
        yield from self._stage_terrain_tiles()
        yield from self._stage_water_texture()
        yield from self._stage_land_texture()
        yield from self._stage_forests()
        yield from self._stage_mountains()
        yield from self._stage_rivers()
        yield from self._stage_settlements()
        yield from self._stage_roads()
        yield from self._stage_bridges()
        yield from self._stage_landmarks()
        yield from self._stage_border_and_compass()
        self._progress("Map complete", 1.0)
        yield

    def _stage_base_noise(self) -> Generator[None, None, None]:
        self._progress("1/12 — Creating elevation and moisture fields", 0.01)
        for row in range(self.rows):
            elevation_row: list[float] = []
            moisture_row: list[float] = []
            land_row: list[bool] = []

            for col in range(self.cols):
                x = col * self.tile + self.tile / 2
                y = row * self.tile + self.tile / 2

                scale = 0.0045 + self.settings.detail * 0.00025
                e = self.noise.fractal(x * scale, y * scale, octaves=6)
                ridges = abs(
                    self.noise.fractal(
                        x * scale * 1.9 + 18.2,
                        y * scale * 1.9 - 11.8,
                        octaves=4,
                    )
                    * 2.0
                    - 1.0
                )
                island = self._radial_island_bias(x, y)
                e = e * 0.57 + island * 0.48 + ridges * 0.12 - 0.11
                m = self.noise.fractal(
                    x * scale * 1.3 + 93.1,
                    y * scale * 1.3 + 47.7,
                    octaves=5,
                )

                elevation_row.append(e)
                moisture_row.append(m)
                land_row.append(e >= 0.40)

            self.elevation.append(elevation_row)
            self.moisture.append(moisture_row)
            self.land_mask.append(land_row)

            if row % 2 == 0:
                self._progress(
                    "1/12 — Creating elevation and moisture fields",
                    0.01 + 0.07 * row / max(1, self.rows - 1),
                )
                yield

    def _stage_terrain_tiles(self) -> Generator[None, None, None]:
        self._progress("2/12 — Painting biomes", 0.09)
        for row in range(self.rows):
            for col in range(self.cols):
                x0 = col * self.tile
                y0 = row * self.tile
                x1 = min(self.settings.width, x0 + self.tile + 1)
                y1 = min(self.settings.height, y0 + self.tile + 1)
                color = self._terrain_color(
                    self.elevation[row][col],
                    self.moisture[row][col],
                )
                self.draw.rectangle((x0, y0, x1, y1), fill=color + (255,))

            self._progress(
                "2/12 — Painting biomes",
                0.09 + 0.13 * row / max(1, self.rows - 1),
            )
            yield

    def _stage_water_texture(self) -> Generator[None, None, None]:
        self._progress("3/12 — Adding water texture", 0.23)
        line_count = int(self.settings.width * self.settings.height / 8500)
        for i in range(line_count):
            x = self.rng.randint(4, self.settings.width - 5)
            y = self.rng.randint(4, self.settings.height - 5)
            if not self._is_land(x, y):
                length = self.rng.randint(5, 18)
                self.draw.arc(
                    (x - length, y - 3, x + length, y + 4),
                    195,
                    345,
                    fill=(205, 231, 232, 95),
                    width=1,
                )
            if i % 18 == 0:
                self._progress(
                    "3/12 — Adding water texture",
                    0.23 + 0.05 * i / max(1, line_count),
                )
                yield

    def _stage_land_texture(self) -> Generator[None, None, None]:
        self._progress("4/12 — Adding grass and soil texture", 0.29)
        dot_count = int(self.settings.width * self.settings.height / 4500)
        for i in range(dot_count):
            x = self.rng.randint(3, self.settings.width - 4)
            y = self.rng.randint(3, self.settings.height - 4)
            e = self._sample_grid(self.elevation, x, y)
            if e >= 0.44 and e < 0.68:
                radius = self.rng.choice((1, 1, 1, 2))
                self.draw.ellipse(
                    (x - radius, y - radius, x + radius, y + radius),
                    fill=(75, 91, 55, self.rng.randint(25, 65)),
                )
            if i % 25 == 0:
                self._progress(
                    "4/12 — Adding grass and soil texture",
                    0.29 + 0.05 * i / max(1, dot_count),
                )
                yield

    def _stage_forests(self) -> Generator[None, None, None]:
        self._progress("5/12 — Growing forests", 0.35)
        candidates: list[tuple[int, int, float]] = []

        for row in range(self.rows):
            for col in range(self.cols):
                e = self.elevation[row][col]
                m = self.moisture[row][col]
                if 0.44 <= e < 0.63 and m > 0.53:
                    candidates.append((col, row, m))

        self.rng.shuffle(candidates)
        max_trees = min(
            len(candidates) * 3,
            int(self.settings.width * self.settings.height / 900),
        )
        drawn = 0

        for index, (col, row, moisture) in enumerate(candidates):
            trees_here = 1 + int((moisture - 0.50) * 7)
            for _ in range(max(1, trees_here)):
                x = int((col + self.rng.random()) * self.tile)
                y = int((row + self.rng.random()) * self.tile)
                if not self._is_land(x, y):
                    continue
                size = self.rng.randint(3, 6)
                trunk = (72, 54, 35, 180)
                canopy = (
                    37 + self.rng.randint(0, 20),
                    77 + self.rng.randint(0, 25),
                    45 + self.rng.randint(0, 15),
                    210,
                )
                self.draw.line((x, y + 2, x, y + size + 3), fill=trunk, width=1)
                self.draw.polygon(
                    (
                        (x, y - size),
                        (x - size, y + size),
                        (x + size, y + size),
                    ),
                    fill=canopy,
                    outline=(31, 59, 37, 170),
                )
                drawn += 1
                if drawn >= max_trees:
                    break

            if index % 10 == 0:
                self._progress(
                    "5/12 — Growing forests",
                    0.35 + 0.09 * index / max(1, len(candidates)),
                )
                yield
            if drawn >= max_trees:
                break

    def _stage_mountains(self) -> Generator[None, None, None]:
        self._progress("6/12 — Raising mountain ranges", 0.45)
        candidates: list[tuple[int, int, float]] = []
        for row in range(self.rows):
            for col in range(self.cols):
                e = self.elevation[row][col]
                if e > 0.64:
                    candidates.append((col, row, e))

        self.rng.shuffle(candidates)
        max_mountains = max(
            12,
            int(self.settings.width * self.settings.height / 8500),
        )
        selected = candidates[: max_mountains * 2]

        for index, (col, row, elev) in enumerate(selected):
            x = int((col + 0.25 + self.rng.random() * 0.5) * self.tile)
            y = int((row + 0.3 + self.rng.random() * 0.4) * self.tile)
            size = self.rng.randint(7, 13)
            height = int(size * (1.2 + max(0.0, elev - 0.65) * 2.0))

            left = (x - size, y + size)
            peak = (x, y - height)
            right = (x + size, y + size)

            self.draw.polygon(
                (left, peak, right),
                fill=(101, 96, 87, 240),
                outline=(67, 62, 57, 220),
            )
            self.draw.polygon(
                (
                    peak,
                    (x - size // 3, y - height // 3),
                    (x, y - height // 6),
                    (x + size // 3, y - height // 3),
                ),
                fill=(223, 223, 213, 220),
            )
            self.draw.line(
                (peak, x - size // 2, y + size // 2),
                fill=(151, 147, 137, 150),
                width=1,
            )
            self.mountain_points.append((x, y))

            if index % 4 == 0:
                self._progress(
                    "6/12 — Raising mountain ranges",
                    0.45 + 0.08 * index / max(1, len(selected)),
                )
                yield

    def _find_high_land_point(self) -> Optional[Point]:
        choices: list[Point] = []
        for _ in range(500):
            x = self.rng.randint(10, self.settings.width - 11)
            y = self.rng.randint(10, self.settings.height - 11)
            e = self._sample_grid(self.elevation, x, y)
            if e > 0.62:
                choices.append((x, y))
        if not choices:
            return None
        return self.rng.choice(choices)

    def _nearest_water_direction(self, x: float, y: float) -> Point:
        best_angle = self.rng.random() * math.tau
        best_score = float("inf")
        for i in range(16):
            angle = i / 16 * math.tau
            tx = x + math.cos(angle) * 60
            ty = y + math.sin(angle) * 60
            if not (0 <= tx < self.settings.width and 0 <= ty < self.settings.height):
                return math.cos(angle), math.sin(angle)
            score = self._sample_grid(self.elevation, tx, ty)
            if score < best_score:
                best_score = score
                best_angle = angle
        return math.cos(best_angle), math.sin(best_angle)

    def _stage_rivers(self) -> Generator[None, None, None]:
        self._progress("7/12 — Carving rivers", 0.54)

        for river_index in range(self.settings.river_count):
            source = self._find_high_land_point()
            if source is None:
                continue

            x, y = source
            path: list[Point] = [(x, y)]
            heading = self.rng.random() * math.tau

            for step in range(180):
                current_e = self._sample_grid(self.elevation, x, y)
                if current_e < 0.405 and step > 10:
                    break

                best: Optional[tuple[float, float, float]] = None
                for turn in (-1.2, -0.8, -0.4, 0, 0.4, 0.8, 1.2):
                    angle = heading + turn
                    nx = x + math.cos(angle) * self.tile * 0.75
                    ny = y + math.sin(angle) * self.tile * 0.75

                    if not (
                        3 <= nx < self.settings.width - 3
                        and 3 <= ny < self.settings.height - 3
                    ):
                        continue

                    elev = self._sample_grid(self.elevation, nx, ny)
                    meander = self.noise.value(nx * 0.025, ny * 0.025) * 0.025
                    score = elev + abs(turn) * 0.006 + meander

                    if best is None or score < best[0]:
                        best = (score, nx, ny)

                if best is None:
                    break

                _, nx, ny = best
                dx = nx - x
                dy = ny - y
                heading = math.atan2(dy, dx)
                x, y = nx, ny
                path.append((x, y))

                if step % 7 == 0 and len(path) > 2:
                    width = max(2, min(7, 2 + len(path) // 25))
                    self.draw.line(
                        path[-8:],
                        fill=self.RIVER + (235,),
                        width=width + 2,
                        joint="curve",
                    )
                    self.draw.line(
                        path[-8:],
                        fill=(92, 166, 198, 245),
                        width=width,
                        joint="curve",
                    )
                    self._progress(
                        f"7/12 — Carving river {river_index + 1}/{self.settings.river_count}",
                        0.54
                        + 0.08
                        * (
                            river_index + step / 180
                        )
                        / max(1, self.settings.river_count),
                    )
                    yield

            if len(path) > 4:
                self.river_paths.append(path)

    def _valid_settlement_point(self, x: int, y: int) -> bool:
        e = self._sample_grid(self.elevation, x, y)
        if not (0.44 <= e < 0.64):
            return False
        for settlement in self.settlements:
            if math.dist((x, y), (settlement.x, settlement.y)) < 70:
                return False
        return True

    def _name_settlement(self) -> str:
        starts = [
            "Ash", "Black", "Bright", "Cinder", "Dawn", "Dragon", "Eagle",
            "Elder", "Frost", "Gold", "Green", "High", "Iron", "Moon",
            "Oak", "Raven", "Red", "River", "Silver", "Stone", "Storm",
            "Thorn", "West", "White", "Wolf",
        ]
        ends = [
            "barrow", "bridge", "brook", "crest", "fall", "ford", "gate",
            "haven", "hold", "mere", "moor", "port", "rest", "ridge",
            "rock", "stead", "vale", "watch", "wick", "wood",
        ]
        return self.rng.choice(starts) + self.rng.choice(ends)

    def _draw_house(self, x: int, y: int, scale: int = 1) -> None:
        w = 7 * scale
        h = 5 * scale
        self.draw.rectangle(
            (x - w, y - h, x + w, y + h),
            fill=(204, 185, 142, 255),
            outline=self.INK + (255,),
            width=max(1, scale),
        )
        self.draw.polygon(
            ((x - w - 2, y - h), (x, y - h - 6 * scale), (x + w + 2, y - h)),
            fill=(117, 70, 52, 255),
            outline=self.INK + (255,),
        )
        self.draw.rectangle(
            (x - scale, y, x + scale, y + h),
            fill=(91, 63, 45, 255),
        )

    def _draw_castle(self, x: int, y: int) -> None:
        self.draw.rectangle(
            (x - 13, y - 9, x + 13, y + 10),
            fill=(163, 157, 143, 255),
            outline=self.INK + (255,),
            width=2,
        )
        for tower_x in (x - 14, x + 14):
            self.draw.rectangle(
                (tower_x - 5, y - 15, tower_x + 5, y + 10),
                fill=(148, 143, 132, 255),
                outline=self.INK + (255,),
                width=2,
            )
            self.draw.polygon(
                (
                    (tower_x - 7, y - 15),
                    (tower_x, y - 23),
                    (tower_x + 7, y - 15),
                ),
                fill=(101, 73, 67, 255),
                outline=self.INK + (255,),
            )
        self.draw.arc(
            (x - 5, y, x + 5, y + 14),
            180,
            360,
            fill=self.INK + (255,),
            width=2,
        )

    def _draw_settlement(self, settlement: Settlement) -> None:
        x, y = settlement.x, settlement.y

        if settlement.kind == "castle":
            self._draw_castle(x, y)
        elif settlement.kind == "town":
            offsets = [(-12, -5), (3, -8), (-4, 9), (14, 7)]
            for ox, oy in offsets:
                self._draw_house(x + ox, y + oy)
            self.draw.ellipse(
                (x - 22, y - 20, x + 22, y + 20),
                outline=(90, 73, 49, 130),
                width=2,
            )
        else:
            offsets = [(-7, 0), (7, 3), (0, -8)]
            for ox, oy in offsets:
                self._draw_house(x + ox, y + oy)

        label_y = y + 18 if settlement.kind != "castle" else y + 15
        self.draw.rounded_rectangle(
            (
                x - len(settlement.name) * 3 - 4,
                label_y,
                x + len(settlement.name) * 3 + 4,
                label_y + 12,
            ),
            radius=3,
            fill=(236, 222, 184, 205),
        )
        self.draw.text(
            (x, label_y + 1),
            settlement.name,
            fill=self.INK + (255,),
            anchor="ma",
        )

    def _stage_settlements(self) -> Generator[None, None, None]:
        self._progress("8/12 — Founding settlements", 0.63)

        desired = self.settings.settlement_count
        attempts = 0

        while len(self.settlements) < desired and attempts < desired * 180:
            attempts += 1
            x = self.rng.randint(35, self.settings.width - 36)
            y = self.rng.randint(35, self.settings.height - 36)

            if not self._valid_settlement_point(x, y):
                continue

            index = len(self.settlements)
            if index == 0:
                kind = "castle"
            elif index % 4 == 0:
                kind = "town"
            else:
                kind = "village"

            settlement = Settlement(x, y, kind, self._name_settlement())
            self.settlements.append(settlement)
            self._draw_settlement(settlement)

            self._progress(
                f"8/12 — Founding {settlement.name}",
                0.63 + 0.07 * len(self.settlements) / max(1, desired),
            )
            yield

    def _curved_road(self, a: Settlement, b: Settlement) -> list[Point]:
        points: list[Point] = []
        steps = max(12, int(math.dist((a.x, a.y), (b.x, b.y)) / 12))
        bend = self.rng.uniform(-0.22, 0.22)
        dx = b.x - a.x
        dy = b.y - a.y
        perp_x = -dy
        perp_y = dx
        length = max(1.0, math.hypot(perp_x, perp_y))
        perp_x /= length
        perp_y /= length

        for i in range(steps + 1):
            t = i / steps
            arc = math.sin(math.pi * t) * bend * math.dist((a.x, a.y), (b.x, b.y))
            jitter = (
                self.noise.value(
                    (a.x + dx * t) * 0.04,
                    (a.y + dy * t) * 0.04,
                )
                - 0.5
            ) * 8
            x = a.x + dx * t + perp_x * (arc + jitter)
            y = a.y + dy * t + perp_y * (arc + jitter)
            points.append((x, y))
        return points

    def _stage_roads(self) -> Generator[None, None, None]:
        self._progress("9/12 — Connecting roads", 0.71)
        if len(self.settlements) < 2:
            yield
            return

        connected = {0}
        remaining = set(range(1, len(self.settlements)))
        edges: list[tuple[int, int]] = []
        while remaining:
            best: Optional[tuple[float, int, int]] = None
            for i in connected:
                for j in remaining:
                    a = self.settlements[i]
                    b = self.settlements[j]
                    distance = math.dist((a.x, a.y), (b.x, b.y))
                    if best is None or distance < best[0]:
                        best = (distance, i, j)
            if best is None:
                break
            _, i, j = best
            edges.append((i, j))
            connected.add(j)
            remaining.remove(j)
        if len(self.settlements) >= 4:
            for _ in range(max(1, len(self.settlements) // 4)):
                i, j = self.rng.sample(range(len(self.settlements)), 2)
                if (i, j) not in edges and (j, i) not in edges:
                    edges.append((i, j))

        for edge_index, (i, j) in enumerate(edges):
            path = self._curved_road(self.settlements[i], self.settlements[j])
            self.road_paths.append(path)
            for point_index in range(2, len(path) + 1, 3):
                segment = path[max(0, point_index - 5):point_index]
                self.draw.line(
                    segment,
                    fill=(74, 58, 42, 130),
                    width=5,
                    joint="curve",
                )
                self.draw.line(
                    segment,
                    fill=self.ROAD + (230,),
                    width=3,
                    joint="curve",
                )
                self._progress(
                    f"9/12 — Building road {edge_index + 1}/{len(edges)}",
                    0.71
                    + 0.08
                    * (
                        edge_index + point_index / max(1, len(path))
                    )
                    / max(1, len(edges)),
                )
                yield

    @staticmethod
    def _segments_intersect(
        a1: Point,
        a2: Point,
        b1: Point,
        b2: Point,
    ) -> Optional[Point]:
        x1, y1 = a1
        x2, y2 = a2
        x3, y3 = b1
        x4, y4 = b2

        denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denominator) < 1e-8:
            return None

        px = (
            (x1 * y2 - y1 * x2) * (x3 - x4)
            - (x1 - x2) * (x3 * y4 - y3 * x4)
        ) / denominator
        py = (
            (x1 * y2 - y1 * x2) * (y3 - y4)
            - (y1 - y2) * (x3 * y4 - y3 * x4)
        ) / denominator

        def inside(p: float, q1: float, q2: float) -> bool:
            return min(q1, q2) - 0.5 <= p <= max(q1, q2) + 0.5

        if (
            inside(px, x1, x2)
            and inside(py, y1, y2)
            and inside(px, x3, x4)
            and inside(py, y3, y4)
        ):
            return px, py
        return None

    def _stage_bridges(self) -> Generator[None, None, None]:
        self._progress("10/12 — Placing bridges", 0.80)
        bridges: list[Point] = []

        for road in self.road_paths:
            for river in self.river_paths:
                for i in range(len(road) - 1):
                    if i % 2:
                        continue
                    for j in range(len(river) - 1):
                        if j % 2:
                            continue
                        intersection = self._segments_intersect(
                            road[i], road[i + 1], river[j], river[j + 1]
                        )
                        if intersection:
                            if all(math.dist(intersection, b) > 20 for b in bridges):
                                bridges.append(intersection)

        for index, (x, y) in enumerate(bridges):
            angle = self.rng.random() * math.pi
            dx = math.cos(angle) * 7
            dy = math.sin(angle) * 7
            px = -math.sin(angle) * 3
            py = math.cos(angle) * 3

            self.draw.line(
                (x - dx, y - dy, x + dx, y + dy),
                fill=(84, 55, 34, 255),
                width=7,
            )
            for rung in (-4, 0, 4):
                rx = math.cos(angle) * rung
                ry = math.sin(angle) * rung
                self.draw.line(
                    (
                        x + rx - px,
                        y + ry - py,
                        x + rx + px,
                        y + ry + py,
                    ),
                    fill=(199, 160, 100, 255),
                    width=2,
                )

            self._progress(
                "10/12 — Placing bridges",
                0.80 + 0.03 * (index + 1) / max(1, len(bridges)),
            )
            yield

    def _stage_landmarks(self) -> Generator[None, None, None]:
        self._progress("11/12 — Adding campaign landmarks", 0.84)
        landmark_names = [
            "Ancient Ruins",
            "Wizard Tower",
            "Dragon Lair",
            "Forgotten Shrine",
            "Cursed Barrow",
            "Old Mine",
            "Bandit Camp",
            "Standing Stones",
        ]
        count = max(4, self.settings.settlement_count // 2)

        kind_map = {
            "Ancient Ruins": "ruin",
            "Wizard Tower": "wizard_tower",
            "Dragon Lair": "cave",
            "Forgotten Shrine": "temple",
            "Cursed Barrow": "dungeon_entrance",
            "Old Mine": "mine",
            "Bandit Camp": "camp",
            "Standing Stones": "standing_stones",
        }
        for index in range(count):
            name = landmark_names[index % len(landmark_names)]
            kind = kind_map.get(name, "landmark")
            placed = False
            for _ in range(240):
                x = self.rng.randint(25, self.settings.width - 26)
                y = self.rng.randint(25, self.settings.height - 26)
                if not self._is_land(x, y):
                    continue
                terrain = self._terrain_color(
                    self._sample_grid(self.elevation, x, y),
                    self._sample_grid(self.moisture, x, y),
                )
                if kind == "cave" and terrain not in {self.HILLS, self.MOUNTAIN, self.FOREST, self.DARK_FOREST}:
                    continue
                if kind == "mine" and terrain not in {self.HILLS, self.MOUNTAIN, self.FOREST, self.DARK_FOREST}:
                    continue
                if kind == "wizard_tower" and terrain in {self.BEACH, self.SNOW}:
                    continue
                if kind == "camp" and terrain in {self.MOUNTAIN, self.SNOW}:
                    continue
                if any(math.dist((x, y), (s.x, s.y)) < 45 for s in self.settlements):
                    continue
                if any(math.dist((x, y), (l.x, l.y)) < 55 for l in self.landmarks):
                    continue
                placed = True
                break

            if not placed:
                continue

            self.landmarks.append(Landmark(x, y, kind, name))
            symbol = index % 4

            if symbol == 0:
                self.draw.rectangle(
                    (x - 8, y - 7, x - 3, y + 8),
                    fill=(111, 106, 96, 220),
                )
                self.draw.rectangle(
                    (x + 2, y - 10, x + 8, y + 7),
                    fill=(111, 106, 96, 220),
                )
                self.draw.line(
                    (x - 12, y + 8, x + 12, y + 8),
                    fill=self.INK + (220,),
                    width=2,
                )
            elif symbol == 1:
                self.draw.ellipse(
                    (x - 7, y - 8, x + 7, y + 8),
                    fill=(126, 119, 111, 255),
                    outline=self.INK + (255,),
                    width=2,
                )
                self.draw.polygon(
                    ((x - 9, y - 7), (x, y - 18), (x + 9, y - 7)),
                    fill=(84, 68, 101, 255),
                    outline=self.INK + (255,),
                )
            elif symbol == 2:
                self.draw.arc(
                    (x - 12, y - 9, x + 12, y + 13),
                    180,
                    360,
                    fill=self.INK + (255,),
                    width=4,
                )
                self.draw.ellipse(
                    (x - 7, y, x + 7, y + 8),
                    fill=(48, 43, 38, 255),
                )
            else:
                self.draw.polygon(
                    ((x - 10, y + 7), (x, y - 10), (x + 10, y + 7)),
                    fill=(158, 119, 77, 255),
                    outline=self.INK + (255,),
                )
                self.draw.line(
                    (x, y - 10, x, y + 7),
                    fill=self.INK + (220,),
                    width=1,
                )

            self.draw.text(
                (x, y + 12),
                name,
                fill=self.INK + (255,),
                anchor="ma",
                stroke_width=2,
                stroke_fill=(232, 218, 182, 210),
            )
            self._progress(
                f"11/12 — Adding {name}",
                0.84 + 0.07 * (index + 1) / count,
            )
            yield

    def _stage_border_and_compass(self) -> Generator[None, None, None]:
        self._progress("12/12 — Inking border and compass rose", 0.92)
        w = self.settings.width
        h = self.settings.height

        self.draw.rectangle(
            (5, 5, w - 6, h - 6),
            outline=(71, 53, 37, 240),
            width=4,
        )
        self.draw.rectangle(
            (10, 10, w - 11, h - 11),
            outline=(139, 106, 68, 190),
            width=1,
        )
        yield

        cx = w - 55
        cy = 55
        radius = 28
        self.draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=(232, 218, 182, 190),
            outline=self.INK + (240,),
            width=2,
        )
        yield

        for i, label in enumerate(("N", "E", "S", "W")):
            angle = -math.pi / 2 + i * math.pi / 2
            tip = (
                cx + math.cos(angle) * 24,
                cy + math.sin(angle) * 24,
            )
            left = (
                cx + math.cos(angle + 2.45) * 8,
                cy + math.sin(angle + 2.45) * 8,
            )
            right = (
                cx + math.cos(angle - 2.45) * 8,
                cy + math.sin(angle - 2.45) * 8,
            )
            self.draw.polygon(
                (tip, left, (cx, cy), right),
                fill=(92, 70, 48, 220) if i % 2 == 0 else (178, 145, 94, 210),
                outline=self.INK + (220,),
            )
            tx = cx + math.cos(angle) * 35
            ty = cy + math.sin(angle) * 35
            self.draw.text(
                (tx, ty),
                label,
                fill=self.INK + (255,),
                anchor="mm",
            )
            self._progress(
                "12/12 — Inking border and compass rose",
                0.93 + 0.05 * (i + 1) / 4,
            )
            yield
        grain = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        grain_draw = ImageDraw.Draw(grain, "RGBA")
        for _ in range(int(w * h / 1800)):
            x = self.rng.randrange(w)
            y = self.rng.randrange(h)
            r = self.rng.choice((1, 1, 2))
            grain_draw.ellipse(
                (x - r, y - r, x + r, y + r),
                fill=(91, 63, 35, self.rng.randint(6, 18)),
            )
        self.image = Image.alpha_composite(
            self.image.convert("RGBA"),
            grain,
        ).convert("RGB")
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        yield
