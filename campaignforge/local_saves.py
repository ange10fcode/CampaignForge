from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class LocalSave:
    path: Path
    name: str
    modified: float
    size: int

    @property
    def display(self) -> str:
        stamp = datetime.fromtimestamp(self.modified).strftime("%Y-%m-%d %H:%M")
        return f"{self.name}  ·  {stamp}"


def saves_directory() -> Path:
    override = os.environ.get("CAMPAIGNFORGE_SAVES")
    if override:
        root = Path(override).expanduser()
    elif getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidate = executable_dir / "CampaignForge Saves"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            root = candidate
        except OSError:
            base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
            root = base / "CampaignForge" / "Saves"
    else:
        base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
        root = base / "CampaignForge" / "Saves"
    root.mkdir(parents=True, exist_ok=True)
    return root


def clean_save_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9 _.-]+", "_", name.strip())
    value = value.strip(" .")[:80] or "campaign"
    return value


def save_path(name: str) -> Path:
    return saves_directory() / f"{clean_save_name(name)}.cforge"


def autosave_path() -> Path:
    return saves_directory() / "autosave.cforge"


def list_local_saves() -> list[LocalSave]:
    out: list[LocalSave] = []
    for path in saves_directory().glob("*.cforge"):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(LocalSave(path, path.stem, stat.st_mtime, stat.st_size))
    out.sort(key=lambda item: item.modified, reverse=True)
    return out
