from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from .assets import Asset, AssetLibrary
from .model import CampaignState, Entity, GenerationConfig, Scene


FORMAT_VERSION = 1


def save_campaign(path: str, state: CampaignState, assets: AssetLibrary) -> None:
    target = Path(path)
    manifest = {
        "format": "CampaignForge Project",
        "version": FORMAT_VERSION,
        "title": state.title,
        "current_id": state.current_id,
        "order": state.order,
        "config": state.config.to_dict(),
        "assets": [],
        "texture_slots": assets.slots,
        "scenes": [],
    }
    with tempfile.TemporaryDirectory(prefix="campaignforge_save_") as tmpdir:
        tmp = Path(tmpdir)
        images_dir = tmp / "scenes"
        images_dir.mkdir()
        for scene_id in state.order:
            scene = state.scenes[scene_id]
            image_name = f"scenes/{scene.id}.png"
            scene.image.save(tmp / image_name, "PNG")
            manifest["scenes"].append(scene.to_manifest(image_name))
        asset_dir = tmp / "assets"
        copied = assets.export_assets(asset_dir)
        for asset in assets.assets.values():
            if asset.id in copied:
                manifest["assets"].append({"id": asset.id, "name": asset.name, "category": asset.category, "pixelation": asset.pixelation, "file": f"assets/{copied[asset.id]}"})
        (tmp / "campaign.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for file in tmp.rglob("*"):
                if file.is_file():
                    z.write(file, file.relative_to(tmp).as_posix())


def load_campaign(path: str) -> tuple[CampaignState, AssetLibrary, str]:
    source = Path(path)
    extraction = tempfile.mkdtemp(prefix="campaignforge_open_")
    root = Path(extraction)
    with zipfile.ZipFile(source, "r") as z:
        z.extractall(root)
    data = json.loads((root / "campaign.json").read_text(encoding="utf-8"))
    state = CampaignState(config=GenerationConfig.from_dict(data.get("config")), title=data.get("title", "Untitled Campaign"))
    for item in data.get("scenes", []):
        image = Image.open(root / item["image"]).convert("RGB")
        scene = Scene(
            id=item["id"],
            title=item["title"],
            kind=item["kind"],
            image=image,
            seed=int(item["seed"]),
            parent_id=item.get("parent_id"),
            source_rect=tuple(item["source_rect"]) if item.get("source_rect") else None,
            biome=item.get("biome", "mixed"),
            semantic=item.get("semantic", item.get("kind", "region")),
            entities=[Entity.from_dict(e) for e in item.get("entities", [])],
            paths=item.get("paths", []),
            metadata=item.get("metadata", {}),
        )
        state.scenes[scene.id] = scene
    state.order = [sid for sid in data.get("order", []) if sid in state.scenes]
    state.current_id = data.get("current_id") if data.get("current_id") in state.scenes else (state.order[0] if state.order else None)
    assets = AssetLibrary()
    for item in data.get("assets", []):
        file_path = root / item["file"]
        asset = Asset(item["id"], item["name"], str(file_path), item.get("category", "custom"), int(item.get("pixelation", 1)))
        assets.assets[asset.id] = asset
    assets.slots = {k: v for k, v in data.get("texture_slots", {}).items() if v in assets.assets}
    return state, assets, extraction
