from __future__ import annotations

import heapq
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
    temperature: int = 50
    max_altitude: int = 70
    moisture: int = 55
    road_min_per_settlement: int = 0
    road_max_per_settlement: int = 3
    road_connection_chance: int = 68
    rare_ruin_chance: int = 4
    smooth_terrain: bool = False

    def __post_init__(self) -> None:
        self.temperature = max(0, min(100, int(self.temperature)))
        self.max_altitude = max(0, min(100, int(self.max_altitude)))
        self.moisture = max(0, min(100, int(self.moisture)))
        self.road_min_per_settlement = max(0, min(8, int(self.road_min_per_settlement)))
        self.road_max_per_settlement = max(self.road_min_per_settlement, min(8, int(self.road_max_per_settlement)))
        self.road_connection_chance = max(0, min(100, int(self.road_connection_chance)))
        self.rare_ruin_chance = max(0, min(25, int(self.rare_ruin_chance)))
        self.smooth_terrain = bool(self.smooth_terrain)


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
    DESERT = (204, 183, 111)
    FOREST = (73, 119, 74)
    DARK_FOREST = (47, 89, 60)
    HILLS = (133, 137, 87)
    MOUNTAIN = (111, 105, 94)
    SNOW = (220, 221, 211)
    WETLANDS = (94, 132, 92)
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
        self.temperature: list[list[float]] = []
        self.land_mask: list[list[bool]] = []
        self.mountain_points: list[Point] = []
        self.settlements: list[Settlement] = []
        self.river_paths: list[list[Point]] = []
        self.road_paths: list[list[Point]] = []
        self.landmarks: list[Landmark] = []

        base_tile = max(4, int(18 - settings.detail * 1.5))
        self.tile = max(2, base_tile // 2) if settings.smooth_terrain else base_tile
        altitude = settings.max_altitude / 100.0
        heat = settings.temperature / 100.0
        self.sea_level = max(0.22, min(0.46, 0.345 + altitude * 0.035 - (heat - 0.5) * 0.11))
        self.coast_level = self.sea_level + 0.045
        self.hill_level = 0.60 + (1.0 - altitude) * 0.16
        self.mountain_level = 0.70 + (1.0 - altitude) * 0.17
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

    def _local_temperature(self, elev: float, x_norm: float = 0.5, y_norm: float = 0.5) -> float:
        base = self.settings.temperature / 100.0
        latitude_cooling = abs(y_norm * 2.0 - 1.0) * 0.15
        elevation_cooling = max(0.0, elev - self.coast_level) * (0.35 + self.settings.max_altitude / 170.0)
        return max(0.0, min(1.0, base - latitude_cooling - elevation_cooling))

    def _terrain_color(self, elev: float, moist: float, temp: Optional[float] = None) -> Color:
        local_temp = self.settings.temperature / 100.0 if temp is None else temp
        if elev < self.sea_level - 0.055:
            return self.DEEP_WATER
        if elev < self.sea_level:
            return self.WATER
        if elev < self.coast_level:
            return self.BEACH
        if elev >= self.mountain_level:
            if local_temp < 0.38 or elev > min(0.97, self.mountain_level + 0.14):
                return self.SNOW
            return self.MOUNTAIN
        if elev >= self.hill_level:
            if local_temp < 0.24:
                return self.SNOW
            return self.HILLS
        if local_temp > 0.72 and moist < 0.48:
            return self.DESERT
        if local_temp > 0.62 and moist < 0.38:
            return self.DRY_PLAINS
        if local_temp < 0.24 and elev > self.coast_level + 0.06:
            return self.SNOW
        if moist > 0.78 and elev < self.hill_level - 0.08 and local_temp > 0.28:
            return self.WETLANDS
        forest_threshold = 0.52 + max(0.0, local_temp - 0.72) * 0.22
        if moist > forest_threshold + 0.14:
            return self.DARK_FOREST
        if moist > forest_threshold:
            return self.FOREST
        if moist < 0.30:
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
        self._progress("1/12 — Creating elevation, moisture and temperature fields", 0.01)
        altitude_strength = 0.25 + (self.settings.max_altitude / 100.0) * 1.05
        heat = self.settings.temperature / 100.0
        for row in range(self.rows):
            elevation_row: list[float] = []
            moisture_row: list[float] = []
            temperature_row: list[float] = []
            land_row: list[bool] = []
            for col in range(self.cols):
                x = col * self.tile + self.tile / 2
                y = row * self.tile + self.tile / 2
                scale = 0.0042 + self.settings.detail * 0.00024
                broad = self.noise.fractal(x * scale, y * scale, octaves=6)
                ridges = 1.0 - abs(self.noise.fractal(x * scale * 1.85 + 18.2, y * scale * 1.85 - 11.8, octaves=4) * 2.0 - 1.0)
                island = self._radial_island_bias(x, y)
                shaped = broad * 0.60 + island * 0.42 + ridges * (0.08 + altitude_strength * 0.08) - 0.07
                e = 0.50 + (shaped - 0.50) * altitude_strength
                if self.settings.max_altitude < 35:
                    e = 0.50 + (e - 0.50) * 0.58
                e = max(0.0, min(1.0, e))
                m = self.noise.fractal(x * scale * 1.28 + 93.1, y * scale * 1.28 + 47.7, octaves=5)
                moisture_bias = (self.settings.moisture - 50) / 100.0
                m = m + moisture_bias * 0.72 - max(0.0, heat - 0.50) * 0.42
                m = max(0.0, min(1.0, m))
                t = self._local_temperature(e, x / max(1, self.settings.width), y / max(1, self.settings.height))
                elevation_row.append(e)
                moisture_row.append(m)
                temperature_row.append(t)
                land_row.append(e >= self.sea_level)
            self.elevation.append(elevation_row)
            self.moisture.append(moisture_row)
            self.temperature.append(temperature_row)
            self.land_mask.append(land_row)
            if row % 2 == 0:
                self._progress("1/12 — Creating elevation, moisture and temperature fields", 0.01 + 0.07 * row / max(1, self.rows - 1))
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
                    self.temperature[row][col],
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
            if e >= self.coast_level and e < self.mountain_level:
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
                t = self.temperature[row][col]
                if self.coast_level <= e < self.mountain_level and m > 0.52 and 0.20 < t < 0.86:
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
        self._progress("6/12 — Shaping mountain ranges", 0.45)
        candidates: list[tuple[int, int, float]] = []
        for row in range(self.rows):
            for col in range(self.cols):
                e = self.elevation[row][col]
                if e > self.hill_level:
                    candidates.append((col, row, e))

        self.rng.shuffle(candidates)
        max_samples = max(12, int(self.settings.width * self.settings.height / 11000))
        for index, (col, row, elev) in enumerate(candidates[:max_samples]):
            x = int((col + 0.5) * self.tile)
            y = int((row + 0.5) * self.tile)
            self.mountain_points.append((x, y))
            if index % 2 == 0:
                shade = int(30 + max(0.0, elev - self.hill_level) * 80)
                span = max(2, self.tile // 3)
                self.draw.line((x-span, y+span//2, x, y-span//2, x+span, y+span//2), fill=(55, 52, 48, shade), width=1)
            if index % 8 == 0:
                self._progress("6/12 — Shaping mountain ranges", 0.45 + 0.08 * index / max(1, min(len(candidates), max_samples)))
                yield

    def _find_high_land_cell(self) -> Optional[tuple[int, int]]:
        threshold = max(self.hill_level, self.coast_level + 0.13)
        candidates: list[tuple[float, int, int]] = []
        for row in range(1, self.rows - 1):
            for col in range(1, self.cols - 1):
                e = self.elevation[row][col]
                if e >= threshold and self.land_mask[row][col]:
                    candidates.append((e + self.rng.random() * 0.08, col, row))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        pool = candidates[: max(8, len(candidates) // 3)]
        _, col, row = self.rng.choice(pool)
        return col, row

    def _river_path_from(self, source: tuple[int, int], occupied: set[tuple[int, int]]) -> list[Point]:
        col, row = source
        visited: set[tuple[int, int]] = set()
        cells: list[tuple[int, int]] = []
        previous_dir: Optional[tuple[int, int]] = None
        for _ in range(max(self.cols, self.rows) * 5):
            if (col, row) in visited:
                break
            visited.add((col, row))
            cells.append((col, row))
            e = self.elevation[row][col]
            if e < self.sea_level + 0.01 and len(cells) > 5:
                break
            if (col, row) in occupied and len(cells) > 6:
                break
            choices: list[tuple[float, int, int, int, int]] = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nc, nr = col + dx, row + dy
                    if not (0 <= nc < self.cols and 0 <= nr < self.rows) or (nc, nr) in visited:
                        continue
                    ne = self.elevation[nr][nc]
                    downhill = ne - e
                    turn_penalty = 0.0
                    if previous_dir is not None:
                        dot = dx * previous_dir[0] + dy * previous_dir[1]
                        turn_penalty = 0.012 if dot <= 0 else (0.004 if dot == 1 else 0.0)
                    merge_bonus = -0.08 if (nc, nr) in occupied else 0.0
                    edge_distance = min(nc, nr, self.cols - 1 - nc, self.rows - 1 - nr)
                    edge_pull = edge_distance * 0.00025
                    noise = (self.noise.value(nc * 0.17 + 41.0, nr * 0.17 - 13.0) - 0.5) * 0.008
                    uphill_penalty = max(0.0, downhill) * 3.5
                    score = ne + uphill_penalty + turn_penalty + edge_pull + noise + merge_bonus
                    choices.append((score, nc, nr, dx, dy))
            if not choices:
                break
            choices.sort(key=lambda item: item[0])
            _, nc, nr, dx, dy = choices[0]
            if self.elevation[nr][nc] > e + 0.055 and len(cells) > 8:
                break
            col, row = nc, nr
            previous_dir = (dx, dy)
        if len(cells) < 5:
            return []
        points: list[Point] = []
        for index, (c, r) in enumerate(cells):
            x = (c + 0.5) * self.tile
            y = (r + 0.5) * self.tile
            if 0 < index < len(cells) - 1:
                pc, pr = cells[index - 1]
                nc, nr = cells[index + 1]
                dx, dy = nc - pc, nr - pr
                ln = max(1.0, math.hypot(dx, dy))
                px, py = -dy / ln, dx / ln
                jitter = (self.noise.value(c * 0.31 + 7.0, r * 0.31 + 19.0) - 0.5) * self.tile * 0.30
                x += px * jitter
                y += py * jitter
            points.append((max(1.0, min(self.settings.width - 2.0, x)), max(1.0, min(self.settings.height - 2.0, y))))
        return points

    def _stage_rivers(self) -> Generator[None, None, None]:
        self._progress("7/12 — Carving terrain-driven rivers", 0.54)
        occupied: set[tuple[int, int]] = set()
        attempts = 0
        made = 0
        if self.settings.river_count > 0:
            target_rivers = self.settings.river_count
        else:
            moisture_factor = self.settings.moisture / 100.0
            altitude_factor = self.settings.max_altitude / 100.0
            area_factor = max(0.55, (self.settings.width * self.settings.height) / (1400 * 900))
            target_rivers = int(round((1.0 + moisture_factor * 6.0 + altitude_factor * 2.4) * math.sqrt(area_factor)))
            if self.settings.moisture < 18:
                target_rivers = max(0, target_rivers - 2)
            target_rivers = max(0, min(14, target_rivers))
        self.metadata_auto_river_count = target_rivers
        while made < target_rivers and attempts < max(10, target_rivers * 14):
            attempts += 1
            source = self._find_high_land_cell()
            if source is None or source in occupied:
                continue
            path = self._river_path_from(source, occupied)
            if len(path) < 5:
                continue
            self.river_paths.append(path)
            for x, y in path:
                occupied.add((max(0, min(self.cols - 1, int(x / self.tile))), max(0, min(self.rows - 1, int(y / self.tile)))))
            width = max(2, min(7, 2 + len(path) // 28))
            for end in range(3, len(path) + 1, 7):
                segment = path[max(0, end - 10):end]
                self.draw.line(segment, fill=self.RIVER + (235,), width=width + 2, joint="curve")
                self.draw.line(segment, fill=(92, 166, 198, 245), width=width, joint="curve")
                self._progress(f"7/12 — Carving river {made + 1}/{target_rivers}", 0.54 + 0.08 * (made + end / max(1, len(path))) / max(1, target_rivers))
                yield
            made += 1

    def _valid_settlement_point(self, x: int, y: int) -> bool:
        e = self._sample_grid(self.elevation, x, y)
        if not (self.coast_level + 0.015 <= e < self.mountain_level - 0.015):
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
        elif settlement.kind == "city":
            offsets = [(-18,-10),(0,-13),(18,-9),(-21,7),(-5,5),(12,8),(24,6),(-10,20),(10,20)]
            for ox, oy in offsets:
                self._draw_house(x + ox, y + oy)
            self.draw.ellipse((x-30,y-27,x+30,y+27), outline=(82,67,47,160), width=3)
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
            elif index % 7 == 1:
                kind = "city"
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

    def _road_cell_cost(self, col: int, row: int, prev: Optional[tuple[int, int]] = None) -> float:
        if not (0 <= col < self.cols and 0 <= row < self.rows):
            return float("inf")
        if not self.land_mask[row][col]:
            return float("inf")
        e = self.elevation[row][col]
        m = self.moisture[row][col]
        t = self.temperature[row][col]
        color = self._terrain_color(e, m, t)
        if color == self.MOUNTAIN or color == self.SNOW:
            base = 8.5
        elif color == self.HILLS:
            base = 3.8
        elif color in {self.FOREST, self.DARK_FOREST}:
            base = 2.2
        elif color == self.DESERT:
            base = 1.8
        elif color == self.BEACH:
            base = 2.7
        else:
            base = 1.0
        if prev is not None:
            pc, pr = prev
            if 0 <= pc < self.cols and 0 <= pr < self.rows:
                slope = abs(e - self.elevation[pr][pc])
                base += slope * (34.0 + self.settings.max_altitude * 0.22)
        return base

    def _road_astar(self, a: Settlement, b: Settlement) -> list[Point]:
        start = (max(0, min(self.cols - 1, int(a.x / self.tile))), max(0, min(self.rows - 1, int(a.y / self.tile))))
        goal = (max(0, min(self.cols - 1, int(b.x / self.tile))), max(0, min(self.rows - 1, int(b.y / self.tile))))
        if start == goal:
            return [(a.x, a.y), (b.x, b.y)]
        queue: list[tuple[float, float, tuple[int, int]]] = [(0.0, 0.0, start)]
        came: dict[tuple[int, int], tuple[int, int]] = {}
        gscore = {start: 0.0}
        visited = 0
        max_visit = max(2500, self.cols * self.rows * 2)
        while queue and visited < max_visit:
            _, current_g, current = heapq.heappop(queue)
            if current_g != gscore.get(current):
                continue
            visited += 1
            if current == goal:
                break
            c, r = current
            for dc, dr in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                nc, nr = c + dc, r + dr
                step_cost = self._road_cell_cost(nc, nr, current)
                if not math.isfinite(step_cost):
                    continue
                diagonal = 1.414 if dc and dr else 1.0
                tentative = current_g + step_cost * diagonal
                node = (nc, nr)
                if tentative >= gscore.get(node, float("inf")):
                    continue
                came[node] = current
                gscore[node] = tentative
                heuristic = math.hypot(goal[0] - nc, goal[1] - nr)
                heapq.heappush(queue, (tentative + heuristic, tentative, node))
        if goal not in came:
            return []
        cells = [goal]
        cur = goal
        while cur != start:
            cur = came[cur]
            cells.append(cur)
        cells.reverse()
        points: list[Point] = [(a.x, a.y)]
        for c, r in cells[1:-1]:
            points.append(((c + 0.5) * self.tile, (r + 0.5) * self.tile))
        points.append((b.x, b.y))
        if len(points) <= 3:
            return points
        smoothed = [points[0]]
        for i in range(1, len(points) - 1):
            px, py = points[i - 1]
            x, y = points[i]
            nx, ny = points[i + 1]
            smoothed.append(((px + x * 2 + nx) / 4.0, (py + y * 2 + ny) / 4.0))
        smoothed.append(points[-1])
        return smoothed

    def _road_edges(self) -> list[tuple[int, int]]:
        n = len(self.settlements)
        if n < 2:
            return []
        min_roads = self.settings.road_min_per_settlement
        max_roads = self.settings.road_max_per_settlement
        if max_roads <= 0:
            return []

        importance = {"city": 4, "castle": 3, "town": 2, "village": 1}
        desired: list[int] = []
        for settlement in self.settlements:
            cap = max_roads
            if settlement.kind == "village":
                cap = min(cap, 2)
            elif settlement.kind == "town":
                cap = min(cap, max(2, max_roads))
            lo = min(min_roads, cap)
            desired.append(self.rng.randint(lo, cap) if cap >= lo else 0)

        degree = [0] * n
        diagonal = math.hypot(self.settings.width, self.settings.height)
        candidates: list[tuple[float, int, int]] = []
        for i in range(n):
            for j in range(i + 1, n):
                a, b = self.settlements[i], self.settlements[j]
                d = math.dist((a.x, a.y), (b.x, b.y))
                imp = max(importance.get(a.kind, 1), importance.get(b.kind, 1))
                max_distance = diagonal * (0.22 + 0.045 * imp)
                long_trade_route = imp >= 3 and d <= diagonal * 0.42 and self.rng.random() < 0.16
                if d > max_distance and not long_trade_route:
                    continue
                score = d / (1.0 + 0.12 * imp) * self.rng.uniform(0.94, 1.08)
                candidates.append((score, i, j))
        candidates.sort()

        edges: list[tuple[int, int]] = []
        edge_set: set[tuple[int, int]] = set()
        for _, i, j in candidates:
            if degree[i] >= desired[i] or degree[j] >= desired[j]:
                continue
            base_chance = self.settings.road_connection_chance
            ai, bj = self.settlements[i], self.settlements[j]
            if ai.kind == "castle" or bj.kind == "castle":
                base_chance = min(100, base_chance + 14)
            if ai.kind == "village" and bj.kind == "village":
                base_chance = max(0, base_chance - 18)
            need = degree[i] < min(min_roads, desired[i]) or degree[j] < min(min_roads, desired[j])
            if not need and self.rng.randrange(100) >= base_chance:
                continue
            pair = (i, j)
            edges.append(pair)
            edge_set.add(pair)
            degree[i] += 1
            degree[j] += 1

        for i in range(n):
            required = min(min_roads, desired[i])
            while degree[i] < required:
                options = []
                for score, a, b in candidates:
                    if i not in (a, b):
                        continue
                    j = b if a == i else a
                    pair = (min(i, j), max(i, j))
                    if pair in edge_set or degree[j] >= desired[j]:
                        continue
                    options.append((score, j))
                if not options:
                    break
                _, j = min(options)
                pair = (min(i, j), max(i, j))
                edges.append(pair)
                edge_set.add(pair)
                degree[i] += 1
                degree[j] += 1
        return edges

    def _stage_roads(self) -> Generator[None, None, None]:
        self._progress("9/12 — Routing terrain-aware roads", 0.71)
        if len(self.settlements) < 2 or self.settings.road_max_per_settlement <= 0:
            yield
            return
        edges = self._road_edges()
        for edge_index, (i, j) in enumerate(edges):
            path = self._road_astar(self.settlements[i], self.settlements[j])
            if len(path) < 2:
                continue
            self.road_paths.append(path)
            for point_index in range(2, len(path) + 1, 5):
                segment = path[max(0, point_index - 8):point_index]
                self.draw.line(segment, fill=(74, 58, 42, 130), width=5, joint="curve")
                self.draw.line(segment, fill=self.ROAD + (230,), width=3, joint="curve")
                self._progress(f"9/12 — Routing road {edge_index + 1}/{max(1, len(edges))}", 0.71 + 0.08 * (edge_index + point_index / max(1, len(path))) / max(1, len(edges)))
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

    def _draw_landmark_symbol(self, x: int, y: int, kind: str) -> None:
        ink = self.INK + (255,)
        if kind in {"wizard_tower"}:
            self.draw.rectangle((x-5,y-8,x+5,y+8),fill=(126,119,111,255),outline=ink,width=2)
            self.draw.polygon(((x-9,y-7),(x,y-17),(x+9,y-7)),fill=(84,68,101,255),outline=ink)
        elif kind == "dragon_lair":
            self.draw.arc((x-12,y-8,x+12,y+13),180,360,fill=ink,width=4)
            self.draw.ellipse((x-7,y,x+7,y+8),fill=(48,43,38,255))
            self.draw.polygon(((x-14,y-4),(x-5,y-1),(x-10,y+6),(x,y+2),(x+10,y+6),(x+5,y-1),(x+14,y-4),(x,y-1)),fill=(145,65,52,220))
        elif kind == "bandit_camp":
            self.draw.polygon(((x-11,y+8),(x,y-10),(x+11,y+8)),fill=(148,124,87,255),outline=ink)
            self.draw.line((x,y-10,x,y+8),fill=(91,66,43,255),width=2)
        elif kind == "fort":
            self.draw.rectangle((x-11,y-9,x+11,y+9),fill=(133,127,116,220),outline=ink,width=2)
            for tx,ty in ((x-11,y-9),(x+11,y-9),(x-11,y+9),(x+11,y+9)):
                self.draw.rectangle((tx-3,ty-3,tx+3,ty+3),fill=(108,103,96,255),outline=ink)
        elif kind == "ruined_city":
            self.draw.line((x-11,y+8,x-11,y-5,x-3,y-5,x-3,y+3,x+4,y+3,x+4,y-8,x+11,y-8,x+11,y+8),fill=(102,96,86,255),width=4)
            self.draw.line((x-13,y+8,x+13,y+8),fill=ink,width=2)
        elif kind in {"cave","mine","dungeon_entrance"}:
            self.draw.arc((x-12,y-9,x+12,y+13),180,360,fill=ink,width=4)
            self.draw.ellipse((x-7,y,x+7,y+8),fill=(48,43,38,255))
        elif kind in {"temple"}:
            self.draw.polygon(((x-11,y+8),(x,y-10),(x+11,y+8)),fill=(177,164,132,255),outline=ink)
            self.draw.rectangle((x-7,y+2,x+7,y+9),fill=(177,164,132,255),outline=ink)
        elif kind == "standing_stones":
            self.draw.rectangle((x-9,y-8,x-4,y+8),fill=(111,106,96,220))
            self.draw.rectangle((x+3,y-11,x+9,y+7),fill=(111,106,96,220))
            self.draw.line((x-13,y+8,x+13,y+8),fill=ink,width=2)
        else:
            self.draw.rectangle((x-8,y-7,x-3,y+8),fill=(111,106,96,220))
            self.draw.rectangle((x+2,y-10,x+8,y+7),fill=(111,106,96,220))
            self.draw.line((x-12,y+8,x+12,y+8),fill=ink,width=2)

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
            "Ruined Settlement",
            "Old Fort",
            "Standing Stones",
            "Broken Watchtower",
            "Abandoned House",
        ]
        base_count = max(2, self.settings.settlement_count // 3)
        extra_ruins = max(0, int(self.cols * self.rows * (self.settings.rare_ruin_chance / 100.0) / 180))
        count = base_count + extra_ruins

        kind_map = {
            "Ancient Ruins": "ruin",
            "Wizard Tower": "wizard_tower",
            "Dragon Lair": "dragon_lair",
            "Forgotten Shrine": "temple",
            "Cursed Barrow": "dungeon_entrance",
            "Old Mine": "mine",
            "Bandit Camp": "bandit_camp",
            "Ruined Settlement": "ruined_city",
            "Old Fort": "fort",
            "Standing Stones": "standing_stones",
            "Broken Watchtower": "ruin",
            "Abandoned House": "ruin",
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
                    self._sample_grid(self.temperature, x, y),
                )
                if kind in {"cave", "dragon_lair"} and terrain not in {self.HILLS, self.MOUNTAIN, self.FOREST, self.DARK_FOREST, self.SNOW}:
                    continue
                if kind == "mine" and terrain not in {self.HILLS, self.MOUNTAIN, self.FOREST, self.DARK_FOREST}:
                    continue
                if kind == "wizard_tower" and terrain in {self.BEACH, self.SNOW}:
                    continue
                if kind == "bandit_camp" and terrain in {self.MOUNTAIN, self.SNOW}:
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
            self._draw_landmark_symbol(x, y, kind)

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
