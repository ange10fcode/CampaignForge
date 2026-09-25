from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass
class Asset:
    id: str
    name: str
    path: str
    category: str = "custom"
    pixelation: int = 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "category": self.category,
            "pixelation": self.pixelation,
        }


class AssetLibrary:
    TEXTURE_SLOTS = [
        "npc.default",
        "building.house",
        "building.tavern",
        "building.blacksmith",
        "building.temple",
        "building.shop",
        "tree",
        "rock",
        "wall.wood",
        "wall.stone",
        "floor.wood",
        "floor.stone",
        "furniture.table",
        "furniture.chair",
        "furniture.bed",
        "object.cart",
        "object.boat",
    ]

    def __init__(self) -> None:
        self.assets: dict[str, Asset] = {}
        self.slots: dict[str, str] = {}
        self.cache: dict[tuple[str, int], Image.Image] = {}
        self.scaled_cache: dict[tuple[str, int, int, int], Image.Image] = {}

    def import_png(self, path: str, category: str = "custom") -> Asset:
        src = Path(path)
        with Image.open(src) as img:
            img.convert("RGBA").load()
        asset = Asset(str(uuid.uuid4()), src.stem, str(src.resolve()), category, 1)
        self.assets[asset.id] = asset
        return asset

    def _clear_asset_cache(self, asset_id: str) -> None:
        self.cache = {k: v for k, v in self.cache.items() if k[0] != asset_id}
        self.scaled_cache = {k: v for k, v in self.scaled_cache.items() if k[0] != asset_id}

    def rename(self, asset_id: str, name: str) -> bool:
        asset = self.assets.get(asset_id)
        clean = name.strip()
        if not asset or not clean:
            return False
        asset.name = clean
        return True

    def set_pixelation(self, asset_id: str, block_size: int) -> bool:
        asset = self.assets.get(asset_id)
        if not asset:
            return False
        asset.pixelation = max(1, min(128, int(block_size)))
        self._clear_asset_cache(asset_id)
        return True

    def reset_pixelation(self, asset_id: str) -> bool:
        return self.set_pixelation(asset_id, 1)

    def get(self, asset_id: str | None) -> Image.Image | None:
        if not asset_id or asset_id not in self.assets:
            return None
        asset = self.assets[asset_id]
        key = (asset_id, asset.pixelation)
        if key in self.cache:
            return self.cache[key].copy()
        try:
            with Image.open(asset.path) as img:
                original = img.convert("RGBA")
        except OSError:
            return None
        if asset.pixelation > 1:
            w, h = original.size
            small = (
                max(1, w // asset.pixelation),
                max(1, h // asset.pixelation),
            )
            original = original.resize(small, Image.Resampling.BOX).resize((w, h), Image.Resampling.NEAREST)
        self.cache[key] = original
        return original.copy()

    def get_scaled(self, asset_id: str | None, width: int, height: int, rotation: int = 0, flip_h: bool = False, flip_v: bool = False) -> Image.Image | None:
        if not asset_id or asset_id not in self.assets:
            return None
        width = max(1, int(width))
        height = max(1, int(height))
        rotation = int(rotation) % 360
        flags = (1 if flip_h else 0) | (2 if flip_v else 0)
        key = (asset_id, width, height, rotation * 4 + flags)
        if key in self.scaled_cache:
            return self.scaled_cache[key].copy()
        img = self.get(asset_id)
        if img is None:
            return None
        if flip_h:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if flip_v:
            img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        img.thumbnail((width, height), Image.Resampling.LANCZOS)
        if rotation:
            img = img.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
        if len(self.scaled_cache) > 96:
            for stale in list(self.scaled_cache)[:32]:
                self.scaled_cache.pop(stale, None)
        self.scaled_cache[key] = img
        return img.copy()

    def get_slot(self, slot: str) -> Image.Image | None:
        return self.get(self.slots.get(slot))

    def assign(self, slot: str, asset_id: str) -> None:
        if asset_id in self.assets:
            self.slots[slot] = asset_id

    def export_assets(self, folder: Path) -> dict[str, str]:
        folder.mkdir(parents=True, exist_ok=True)
        names = {}
        for asset in self.assets.values():
            src = Path(asset.path)
            if not src.exists():
                continue
            dest = folder / f"{asset.id}{src.suffix.lower()}"
            shutil.copy2(src, dest)
            names[asset.id] = dest.name
        return names
