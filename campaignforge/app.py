from __future__ import annotations

import json
import math
import random
import tempfile
import uuid
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import tkinter as tk
from tkinter import ttk
from typing import Generator, Optional

from PIL import Image, ImageDraw, ImageTk

from .world_engine import MapSettings

from .assets import AssetLibrary
from .biomes import WATER_BIOMES, rect_aspect_dimensions
from .context import SelectionContext, context_for_entity, resolve_selection, road_entrances_for_entity
from .generators import (
    BuildingInteriorGenerator,
    CastleGenerator,
    CaveGenerator,
    ContextRegionGenerator,
    DetailSettings,
    MageTowerGenerator,
    OceanGenerator,
    RoadEncounterGenerator,
    RoomGenerator,
    TownGenerator,
    WildernessGenerator,
    WorldSceneGenerator,
)
from .model import CampaignState, Entity, GenerationConfig, Scene
from .persistence import load_campaign, save_campaign


class CampaignForgeApp:
    WORLD_PRESETS = {
        "Small · 900 × 650": (900, 650),
        "Medium · 1400 × 900": (1400, 900),
        "Large · 1800 × 1200": (1800, 1200),
        "Epic · 2400 × 1600": (2400, 1600),
        "Custom": None,
    }
    DETAIL_PRESETS = {
        "Encounter · up to 1000 × 800": (1000, 800),
        "Detailed · up to 1500 × 1100": (1500, 1100),
        "High Detail · up to 2000 × 1500": (2000, 1500),
    }
    NPC_TYPES = ["Villager", "Guard", "Merchant", "Traveler", "Mage", "Bandit"]
    NPC_COLORS = {
        "Villager": "#d3a35d",
        "Guard": "#7c9fc7",
        "Merchant": "#c78fbd",
        "Traveler": "#9ac17f",
        "Mage": "#9a7bd1",
        "Bandit": "#b96d67",
    }

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("CampaignForge 1.0 · Context-Aware World Builder")
        self.root.geometry("1580x960")
        self.root.minsize(1120, 720)
        self.state = CampaignState()
        self.assets = AssetLibrary()
        self.project_extract_dir: Optional[str] = None
        self.generator = None
        self.steps: Optional[Generator] = None
        self.pending: dict = {}
        self.running = False
        self.paused = False
        self.after_id: Optional[str] = None
        self.zoom = 1.0
        self.selection: Optional[tuple[int, int, int, int]] = None
        self.drag_start: Optional[tuple[float, float]] = None
        self.drag_item = None
        self.entity_drag_id: Optional[str] = None
        self.entity_drag_offset = (0.0, 0.0)
        self.selected_entity_id: Optional[str] = None
        self.pending_place: Optional[tuple[str, str]] = None
        self.photo: Optional[ImageTk.PhotoImage] = None
        self.overlay_photos: list[ImageTk.PhotoImage] = []
        self.asset_preview_photo: Optional[ImageTk.PhotoImage] = None
        self.transition_photos: list[ImageTk.PhotoImage] = []
        self._scaled_render_cache: dict[tuple, Image.Image] = {}
        self._base_render_cache: dict[tuple, Image.Image] = {}
        self._quality_after_id: Optional[str] = None
        self._drag_render_after_id: Optional[str] = None
        self._last_render_key: Optional[tuple] = None
        self._style()
        self._build_ui()
        self._random_seed()
        self.root.after(220, self.new_world)

    def _style(self) -> None:
        self.root.configure(bg="#15181d")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        bg = "#171a20"
        panel = "#20242b"
        field = "#292e37"
        fg = "#e6e9ef"
        muted = "#aab1bd"
        accent = "#5b8def"
        style.configure(".", background=bg, foreground=fg, fieldbackground=field, bordercolor="#343a45")
        style.configure("TFrame", background=bg)
        style.configure("Panel.TFrame", background=panel)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Panel.TLabel", background=panel, foreground=fg)
        style.configure("Muted.TLabel", foreground=muted)
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"), foreground="#f3f5f8")
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"), foreground="#f0f2f6")
        style.configure("TButton", background=field, foreground=fg, padding=(8, 6))
        style.map("TButton", background=[("active", "#353c48"), ("pressed", "#404958")])
        style.configure("Accent.TButton", background=accent, foreground="white", padding=(9, 7))
        style.map("Accent.TButton", background=[("active", "#6e9af0"), ("pressed", "#477bd7")])
        style.configure("TNotebook", background=panel, borderwidth=0)
        style.configure("TNotebook.Tab", background="#252a32", foreground=fg, padding=(9, 6))
        style.map("TNotebook.Tab", background=[("selected", "#343b46")])
        style.configure("Treeview", background="#1c2026", fieldbackground="#1c2026", foreground=fg, rowheight=26)
        style.map("Treeview", background=[("selected", "#3d5f9e")])
        style.configure("TCheckbutton", background=panel, foreground=fg)
        style.configure("TCombobox", fieldbackground=field, foreground=fg)
        style.configure("TSpinbox", fieldbackground=field, foreground=fg)
        style.configure("TEntry", fieldbackground=field, foreground=fg)

    def _build_ui(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, style="Panel.TFrame", padding=10, width=320)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.rowconfigure(2, weight=1)
        sidebar.columnconfigure(0, weight=1)

        main = ttk.Frame(self.root, padding=(0, 8, 8, 8))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        ttk.Label(sidebar, text="CampaignForge", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.context_label = ttk.Label(sidebar, text="World → Region → Town → Building → Room", style="Panel.TLabel")
        self.context_label.grid(row=1, column=0, sticky="w", pady=(0, 10))

        self.notebook = ttk.Notebook(sidebar, width=300)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        self.gen_tab = ttk.Frame(self.notebook, padding=10, style="Panel.TFrame")
        self.struct_tab = ttk.Frame(self.notebook, padding=10, style="Panel.TFrame")
        self.npc_tab = ttk.Frame(self.notebook, padding=10, style="Panel.TFrame")
        self.asset_tab = ttk.Frame(self.notebook, padding=10, style="Panel.TFrame")
        self.transform_tab = ttk.Frame(self.notebook, padding=10, style="Panel.TFrame")
        self.campaign_tab = ttk.Frame(self.notebook, padding=10, style="Panel.TFrame")
        self.notebook.add(self.gen_tab, text="Generate")
        self.notebook.add(self.struct_tab, text="Rules")
        self.notebook.add(self.npc_tab, text="NPCs")
        self.notebook.add(self.asset_tab, text="Assets")
        self.notebook.add(self.transform_tab, text="Transform")
        self.notebook.add(self.campaign_tab, text="Campaign")
        self._build_generation_tab()
        self._build_rules_tab()
        self._build_npc_tab()
        self._build_assets_tab()
        self._build_transform_tab()
        self._build_campaign_tab()

        toolbar = ttk.Frame(main)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        toolbar.columnconfigure(10, weight=1)
        ttk.Button(toolbar, text="Select Area", command=self.select_mode).grid(row=0, column=0, padx=(0, 3))
        ttk.Button(toolbar, text="Generate Detail", style="Accent.TButton", command=self.make_detail).grid(row=0, column=1, padx=3)
        ttk.Button(toolbar, text="Back", command=self.go_parent).grid(row=0, column=2, padx=3)
        ttk.Button(toolbar, text="Fit", command=self.fit_view).grid(row=0, column=3, padx=3)
        ttk.Button(toolbar, text="−", width=3, command=lambda: self.change_zoom(.82)).grid(row=0, column=4, padx=(9, 2))
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.change_zoom(1.22)).grid(row=0, column=5, padx=2)
        self.zoom_label = ttk.Label(toolbar, text="100%")
        self.zoom_label.grid(row=0, column=6, padx=(5, 12))
        ttk.Label(toolbar, text="Double-click places to enter · wheel zooms").grid(row=0, column=7, padx=6)
        self.layer_label = ttk.Label(toolbar, text="", anchor="e")
        self.layer_label.grid(row=0, column=10, sticky="e")

        viewport = ttk.Frame(main)
        viewport.grid(row=1, column=0, sticky="nsew")
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(viewport, bg="#101216", highlightthickness=1, highlightbackground="#303641", cursor="crosshair")
        self.xbar = ttk.Scrollbar(viewport, orient="horizontal", command=self.canvas.xview)
        self.ybar = ttk.Scrollbar(viewport, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.xbar.set, yscrollcommand=self.ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.ybar.grid(row=0, column=1, sticky="ns")
        self.xbar.grid(row=1, column=0, sticky="ew")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._double_click)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._wheel_linux(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel_linux(e, -1))
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<ButtonPress-3>", self._pan_start)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<Control-MouseWheel>", self._resize_entity_wheel)

        status = ttk.Frame(main)
        status.grid(row=2, column=0, sticky="ew", pady=(7, 0))
        status.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        self.progress = tk.DoubleVar(value=0)
        ttk.Progressbar(status, variable=self.progress, maximum=100, length=240).grid(row=0, column=1, padx=(10, 0))

    def _build_generation_tab(self) -> None:
        t = self.gen_tab
        t.columnconfigure(0, weight=1)
        ttk.Label(t, text="World", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.world_preset = tk.StringVar(value="Medium · 1400 × 900")
        preset = ttk.Combobox(t, textvariable=self.world_preset, values=list(self.WORLD_PRESETS), state="readonly")
        preset.grid(row=1, column=0, sticky="ew", pady=(5, 4))
        preset.bind("<<ComboboxSelected>>", self._apply_world_preset)
        dims = ttk.Frame(t, style="Panel.TFrame")
        dims.grid(row=2, column=0, sticky="ew")
        dims.columnconfigure((0, 1), weight=1)
        self.world_w = tk.IntVar(value=1400)
        self.world_h = tk.IntVar(value=900)
        ttk.Spinbox(dims, from_=600, to=3200, increment=50, textvariable=self.world_w).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Spinbox(dims, from_=450, to=2400, increment=50, textvariable=self.world_h).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.seed_var = tk.StringVar()
        seed = ttk.Frame(t, style="Panel.TFrame")
        seed.grid(row=3, column=0, sticky="ew", pady=(7, 3))
        seed.columnconfigure(0, weight=1)
        ttk.Entry(seed, textvariable=self.seed_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(seed, text="Random", command=self._random_seed).grid(row=0, column=1, padx=(5, 0))
        self.world_detail = tk.IntVar(value=6)
        self.world_rivers = tk.IntVar(value=5)
        self.world_towns = tk.IntVar(value=12)
        self._labeled_spin(t, "Terrain detail", self.world_detail, 1, 8, 4)
        self._labeled_spin(t, "Rivers", self.world_rivers, 0, 16, 6)
        self._labeled_spin(t, "Settlements", self.world_towns, 0, 30, 8)
        ttk.Button(t, text="Generate New World", style="Accent.TButton", command=self.new_world).grid(row=10, column=0, sticky="ew", pady=(11, 3))

        ttk.Separator(t).grid(row=11, column=0, sticky="ew", pady=10)
        ttk.Label(t, text="Detail generation", style="Section.TLabel").grid(row=12, column=0, sticky="w")
        self.detail_preset = tk.StringVar(value="Detailed · up to 1500 × 1100")
        ttk.Combobox(t, textvariable=self.detail_preset, values=list(self.DETAIL_PRESETS), state="readonly").grid(row=13, column=0, sticky="ew", pady=(5, 3))
        self.detail_density = tk.IntVar(value=7)
        self.detail_structures = tk.IntVar(value=18)
        self.detail_landmarks = tk.IntVar(value=7)
        self._labeled_spin(t, "Local density", self.detail_density, 1, 10, 14)
        self._labeled_spin(t, "Structures", self.detail_structures, 0, 60, 16)
        self._labeled_spin(t, "Landmarks", self.detail_landmarks, 0, 25, 18)
        self.speed_var = tk.IntVar(value=82)
        ttk.Label(t, text="Generation speed", style="Panel.TLabel").grid(row=20, column=0, sticky="w", pady=(7, 0))
        ttk.Scale(t, from_=1, to=100, variable=self.speed_var, orient="horizontal").grid(row=21, column=0, sticky="ew")
        self.pause_btn = ttk.Button(t, text="Pause", state="disabled", command=self.toggle_pause)
        self.pause_btn.grid(row=22, column=0, sticky="ew", pady=(10, 3))
        self.step_btn = ttk.Button(t, text="Single Step", state="disabled", command=self.single_step)
        self.step_btn.grid(row=23, column=0, sticky="ew", pady=3)

    def _build_rules_tab(self) -> None:
        t = self.struct_tab
        t.columnconfigure(0, weight=1)
        ttk.Label(t, text="Generation rules", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(t, text="Biome restrictions are always enforced. These switches control which categories may be generated.", wraplength=260, style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 8))
        self.rule_vars: dict[str, tk.BooleanVar] = {}
        labels = [
            ("towns", "Towns"), ("houses", "Houses & civilian buildings"), ("roads", "Roads & paths"),
            ("ruins", "Ruins & caves"), ("dungeons", "Dungeons"), ("castles", "Castles & forts"),
            ("npcs", "NPCs"), ("boats", "Boats & shipwrecks"), ("carts", "Carts & road props"),
            ("furniture", "Furniture"), ("wildlife", "Wildlife"), ("decorations", "Biome decorations"),
            ("ocean_structures", "Ocean structures"), ("underground", "Underground structures"),
        ]
        for row, (key, label) in enumerate(labels, start=2):
            var = tk.BooleanVar(value=getattr(self.state.config, key))
            self.rule_vars[key] = var
            ttk.Checkbutton(t, text=label, variable=var, command=self._sync_config).grid(row=row, column=0, sticky="w", pady=1)
        ttk.Separator(t).grid(row=17, column=0, sticky="ew", pady=10)
        ttk.Label(t, text="Grid overlay", style="Section.TLabel").grid(row=18, column=0, sticky="w")
        self.grid_type = tk.StringVar(value="None")
        ttk.Combobox(t, textvariable=self.grid_type, values=["None", "Square", "Hex"], state="readonly").grid(row=19, column=0, sticky="ew", pady=(4, 3))
        self.grid_size = tk.IntVar(value=48)
        self._labeled_spin(t, "Cell size", self.grid_size, 18, 140, 20)
        ttk.Button(t, text="Refresh Grid", command=self.render).grid(row=22, column=0, sticky="ew", pady=(5, 0))

    def _build_npc_tab(self) -> None:
        t = self.npc_tab
        t.columnconfigure(0, weight=1)
        ttk.Label(t, text="NPC palette", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(t, text="Drag a token from this palette onto the map, or choose one and click Place NPC.", wraplength=260, style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 8))
        self.npc_palette = tk.Canvas(t, height=245, bg="#1b1f25", highlightthickness=1, highlightbackground="#353b45")
        self.npc_palette.grid(row=2, column=0, sticky="ew")
        self._draw_npc_palette()
        self.npc_palette.bind("<ButtonPress-1>", self._npc_palette_press)
        self.npc_palette.bind("<ButtonRelease-1>", self._npc_palette_release)
        self.npc_choice = tk.StringVar(value=self.NPC_TYPES[0])
        ttk.Combobox(t, textvariable=self.npc_choice, values=self.NPC_TYPES, state="readonly").grid(row=3, column=0, sticky="ew", pady=(8, 3))
        ttk.Button(t, text="Place NPC", command=lambda: self._arm_place("npc", self.npc_choice.get())).grid(row=4, column=0, sticky="ew", pady=3)
        ttk.Separator(t).grid(row=5, column=0, sticky="ew", pady=10)
        ttk.Label(t, text="Select a placed NPC or PNG on the map, then use the Transform tab for scaling, rotation, flips, and exact dimensions.", wraplength=260, style="Panel.TLabel").grid(row=6, column=0, sticky="w")
        ttk.Button(t, text="Delete Selected Object", command=self.delete_selected_entity).grid(row=7, column=0, sticky="ew", pady=(8, 3))

    def _build_assets_tab(self) -> None:
        t = self.asset_tab
        t.columnconfigure(0, weight=1)
        ttk.Label(t, text="Custom PNG assets", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(t, text="Import PNG", command=self.import_png).grid(row=1, column=0, sticky="ew", pady=(6, 4))
        self.asset_list = tk.Listbox(t, height=7, bg="#1c2026", fg="#e7e9ee", selectbackground="#3d5f9e", borderwidth=0, highlightthickness=1, highlightbackground="#343a45", exportselection=False)
        self.asset_list.grid(row=2, column=0, sticky="ew")
        self.asset_list.bind("<<ListboxSelect>>", self._asset_selected)
        self.asset_preview = ttk.Label(t, text="No asset selected", anchor="center", style="Panel.TLabel")
        self.asset_preview.grid(row=3, column=0, sticky="ew", pady=(6, 4))
        asset_buttons = ttk.Frame(t, style="Panel.TFrame")
        asset_buttons.grid(row=4, column=0, sticky="ew")
        asset_buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(asset_buttons, text="Place on Map", command=self.arm_selected_asset).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(asset_buttons, text="Rename", command=self.rename_selected_asset).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(t, text="Pixelation", style="Panel.TLabel").grid(row=5, column=0, sticky="w", pady=(9, 0))
        px = ttk.Frame(t, style="Panel.TFrame")
        px.grid(row=6, column=0, sticky="ew")
        px.columnconfigure(0, weight=1)
        self.asset_pixelation = tk.IntVar(value=1)
        ttk.Spinbox(px, from_=1, to=64, textvariable=self.asset_pixelation).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(px, text="Apply", command=self.apply_asset_pixelation).grid(row=0, column=1, padx=3)
        ttk.Button(px, text="Original", command=self.reset_asset_pixelation).grid(row=0, column=2, padx=(3, 0))
        ttk.Separator(t).grid(row=7, column=0, sticky="ew", pady=10)
        ttk.Label(t, text="Replace generator texture", style="Section.TLabel").grid(row=8, column=0, sticky="w")
        self.texture_slot = tk.StringVar(value=AssetLibrary.TEXTURE_SLOTS[0])
        slot_box = ttk.Combobox(t, textvariable=self.texture_slot, values=AssetLibrary.TEXTURE_SLOTS, state="readonly")
        slot_box.grid(row=9, column=0, sticky="ew", pady=(5, 3))
        slot_box.bind("<<ComboboxSelected>>", self._texture_slot_changed)
        ttk.Button(t, text="Assign Selected PNG", command=self.assign_texture).grid(row=10, column=0, sticky="ew", pady=3)
        self.texture_status = ttk.Label(t, text="Built-in texture", style="Panel.TLabel")
        self.texture_status.grid(row=11, column=0, sticky="w", pady=(5, 0))

    def _build_transform_tab(self) -> None:
        t = self.transform_tab
        t.columnconfigure(0, weight=1)
        ttk.Label(t, text="Object transform", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(t, text="Select a movable NPC or imported PNG on the map. Transform changes are persistent and do not modify the original PNG.", wraplength=260, style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 9))
        scale = ttk.Frame(t, style="Panel.TFrame")
        scale.grid(row=2, column=0, sticky="ew")
        scale.columnconfigure((0, 1), weight=1)
        ttk.Button(scale, text="Scale −", command=lambda: self.resize_selected(.88)).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(scale, text="Scale +", command=lambda: self.resize_selected(1.14)).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        rotate = ttk.Frame(t, style="Panel.TFrame")
        rotate.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        rotate.columnconfigure((0, 1), weight=1)
        ttk.Button(rotate, text="Rotate −15°", command=lambda: self.rotate_selected(-15)).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(rotate, text="Rotate +15°", command=lambda: self.rotate_selected(15)).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        flips = ttk.Frame(t, style="Panel.TFrame")
        flips.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        flips.columnconfigure((0, 1), weight=1)
        ttk.Button(flips, text="Flip Horizontal", command=lambda: self.flip_selected("h")).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(flips, text="Flip Vertical", command=lambda: self.flip_selected("v")).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Separator(t).grid(row=5, column=0, sticky="ew", pady=10)
        ttk.Label(t, text="Exact size", style="Section.TLabel").grid(row=6, column=0, sticky="w")
        size = ttk.Frame(t, style="Panel.TFrame")
        size.grid(row=7, column=0, sticky="ew", pady=(5, 3))
        size.columnconfigure((0, 1), weight=1)
        self.transform_w = tk.DoubleVar(value=32)
        self.transform_h = tk.DoubleVar(value=32)
        ttk.Spinbox(size, from_=4, to=4000, increment=1, textvariable=self.transform_w).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Spinbox(size, from_=4, to=4000, increment=1, textvariable=self.transform_h).grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.keep_aspect = tk.BooleanVar(value=True)
        ttk.Checkbutton(t, text="Maintain aspect ratio", variable=self.keep_aspect).grid(row=8, column=0, sticky="w", pady=3)
        ttk.Button(t, text="Apply Size", command=self.apply_transform_size).grid(row=9, column=0, sticky="ew", pady=3)
        ttk.Button(t, text="Reset Transform", command=self.reset_selected_transform).grid(row=10, column=0, sticky="ew", pady=3)
        ttk.Button(t, text="Delete Selected Object", command=self.delete_selected_entity).grid(row=11, column=0, sticky="ew", pady=(9, 3))
        self.transform_status = ttk.Label(t, text="No object selected", wraplength=260, style="Panel.TLabel")
        self.transform_status.grid(row=12, column=0, sticky="w", pady=(7, 0))

    def _build_campaign_tab(self) -> None:
        t = self.campaign_tab
        t.columnconfigure(0, weight=1)
        t.rowconfigure(2, weight=1)
        ttk.Label(t, text="Persistent world", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(t, text="Generated scenes remain linked to their parent locations. Double-click a scene below to jump to it.", wraplength=260, style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 8))
        self.scene_tree = ttk.Treeview(t, show="tree", height=15)
        self.scene_tree.grid(row=2, column=0, sticky="nsew")
        self.scene_tree.bind("<Double-1>", self._tree_open)
        buttons = ttk.Frame(t, style="Panel.TFrame")
        buttons.grid(row=3, column=0, sticky="ew", pady=(7, 0))
        buttons.columnconfigure((0, 1), weight=1)
        ttk.Button(buttons, text="Save Project", command=self.save_project).grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=2)
        ttk.Button(buttons, text="Open Project", command=self.open_project).grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=2)
        ttk.Button(buttons, text="Export PNG", command=self.export_png).grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=2)
        ttk.Button(buttons, text="Export Pack", command=self.export_pack).grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=2)
        ttk.Button(t, text="Rename Current Scene", command=self.rename_scene).grid(row=4, column=0, sticky="ew", pady=(6, 2))
        ttk.Button(t, text="Delete Selected Sub-Area", command=self.delete_selected_scene).grid(row=5, column=0, sticky="ew", pady=2)

    def _labeled_spin(self, parent, label, var, lo, hi, row) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(parent, from_=lo, to=hi, textvariable=var).grid(row=row+1, column=0, sticky="ew")

    def _apply_world_preset(self, _event=None) -> None:
        value = self.WORLD_PRESETS.get(self.world_preset.get())
        if value:
            self.world_w.set(value[0])
            self.world_h.set(value[1])

    def _sync_config(self) -> None:
        for key, var in self.rule_vars.items():
            setattr(self.state.config, key, bool(var.get()))

    def _random_seed(self) -> None:
        self.seed_var.set(str(random.SystemRandom().randint(1, 2_147_483_647)))

    def _seed(self) -> int:
        try:
            return int(self.seed_var.get().strip())
        except ValueError:
            self._random_seed()
            return int(self.seed_var.get())

    def _detail_settings_for_rect(self, rect: tuple[int, int, int, int], seed: int) -> DetailSettings:
        max_w, max_h = self.DETAIL_PRESETS[self.detail_preset.get()]
        w, h = rect_aspect_dimensions(rect, max_w, max_h, minimum=520)
        return DetailSettings(w, h, seed, int(self.detail_density.get()), int(self.detail_structures.get()), int(self.detail_landmarks.get()))

    def _status(self, text: str, value: float) -> None:
        self.status_var.set(text)
        self.progress.set(max(0, min(100, value * 100)))

    def _delay(self) -> int:
        return max(1, int(150 - float(self.speed_var.get()) * 1.45))

    def new_world(self) -> None:
        self._sync_config()
        seed = self._seed()
        settings = MapSettings(int(self.world_w.get()), int(self.world_h.get()), seed, int(self.world_detail.get()), int(self.world_rivers.get()), int(self.world_towns.get()))
        generator = WorldSceneGenerator(settings, self.state.config, self._status)
        self.state = CampaignState(config=self.state.config, title=self.state.title)
        self._invalidate_render_cache()
        self.selection = None
        self.selected_entity_id = None
        self._start_generation(generator, "World Map", "world", seed, None, None, "mixed", "world")

    def make_detail(self) -> None:
        scene = self.state.current()
        if not scene:
            return
        if scene.kind == "room":
            self.status_var.set("Room detail is the deepest generated level. Add, move, or resize NPCs and custom PNG objects here.")
            return
        rect = self._selection_for(scene)
        if not rect:
            messagebox.showinfo("Select an area", "Drag a rectangle over the map first, or double-click a town/building directly.")
            return
        context = resolve_selection(scene, rect)
        self._status(f"Context: {context.semantic} · {context.biome} · {context.reason}", 0)
        self._generate_context(scene, context, rect)

    def _generate_context(self, parent: Scene, context: SelectionContext, source_rect: Optional[tuple[int, int, int, int]]) -> None:
        self._sync_config()
        seed = random.SystemRandom().randint(1, 2_147_483_647)
        rect = source_rect or self._entity_rect(parent, context.focus_entity)
        settings = self._detail_settings_for_rect(rect, seed)
        settings.lod = context.lod
        settings.selection_fraction = context.selection_fraction
        focus = context.focus_entity
        entrances = list(context.road_entrances)
        if focus and not entrances:
            entrances = road_entrances_for_entity(parent, focus)

        if context.semantic == "town" and focus:
            settings.width = max(settings.width, 1100)
            settings.height = max(settings.height, 800)
            settings.lod = 3
            generator = TownGenerator(focus, settings, self.state.config, self.assets, self._status, entrances)
            title = focus.name
            kind = "town"
        elif context.semantic == "castle" and focus:
            settings.width = max(settings.width, 1200)
            settings.height = max(settings.height, 900)
            settings.lod = 3
            generator = CastleGenerator(focus, settings, self.state.config, self.assets, self._status, entrances)
            title = focus.name
            kind = "castle"
        elif context.semantic == "mage_tower" and focus:
            settings.width = max(settings.width, 1050)
            settings.height = max(settings.height, 800)
            settings.lod = 3
            generator = MageTowerGenerator(focus, settings, self.state.config, self.assets, self._status, entrances)
            title = focus.name
            kind = "mage_tower"
        elif context.semantic == "cave" and focus:
            settings.width = max(settings.width, 1050)
            settings.height = max(settings.height, 780)
            settings.lod = 3
            generator = CaveGenerator(focus, settings, self.state.config, self.assets, self._status, entrances)
            title = focus.name
            kind = "cave"
        elif context.semantic == "building" and focus:
            generator = BuildingInteriorGenerator(focus, 1200, 900, seed, self.state.config, self.assets, self._status)
            title = f"{focus.name} · Interior"
            kind = "building"
        elif context.semantic == "room" and focus:
            generator = RoomGenerator(focus, 1000, 760, seed, self.state.config, self.assets, self._status)
            title = focus.name
            kind = "room"
        elif context.semantic == "road":
            generator = RoadEncounterGenerator(context.biome, settings, self.state.config, self.assets, self._status, context.road_angle)
            title = "Road Encounter"
            kind = "road"
        elif context.semantic == "ocean" or context.biome in WATER_BIOMES:
            generator = OceanGenerator(context.biome, settings, self.state.config, self.assets, self._status)
            title = "Ocean Detail"
            kind = "ocean"
        elif context.semantic == "region":
            generator = ContextRegionGenerator(parent, rect, settings, self.state.config, self.assets, self._status)
            location_count = len(context.focus_entities)
            title = f"{parent.title} · Area" if location_count > 1 else f"{parent.title} · Region"
            kind = "region"
        else:
            generator = WildernessGenerator(context.biome, settings, self.state.config, self.assets, self._status)
            title = f"{context.biome.replace('_', ' ').title()} Detail"
            kind = "terrain"
        focus_id = focus.id if focus else None
        self._start_generation(generator, title, kind, seed, parent.id, rect, context.biome, context.semantic, focus_id)

    def _start_generation(self, generator, title: str, kind: str, seed: int, parent_id: Optional[str], source_rect, biome: str, semantic: str, focus_entity_id: Optional[str] = None) -> None:
        self._cancel_tick()
        self.generator = generator
        self.steps = generator.generate()
        self.pending = {
            "title": title, "kind": kind, "seed": seed, "parent_id": parent_id, "source_rect": source_rect,
            "biome": biome, "semantic": semantic, "focus_entity_id": focus_entity_id,
        }
        self.running = True
        self.paused = False
        self.pause_btn.configure(text="Pause", state="normal")
        self.step_btn.configure(state="disabled")
        self._tick(1)

    def _tick(self, delay: Optional[int] = None) -> None:
        if self.running and not self.paused:
            self.after_id = self.root.after(self._delay() if delay is None else delay, self._advance)

    def _cancel_tick(self) -> None:
        if self.after_id:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.after_id = None

    def _advance(self) -> None:
        self.after_id = None
        if not self.running or self.paused or not self.steps:
            return
        try:
            next(self.steps)
            self._preview_generator()
            self._tick()
        except StopIteration:
            self._finish_generation()
        except Exception as exc:
            self.running = False
            self.pause_btn.configure(state="disabled")
            messagebox.showerror("Generation error", str(exc))
            raise

    def _preview_generator(self) -> None:
        if not self.generator or not getattr(self.generator, "image", None):
            return
        temp = Scene("preview", "Generating…", "preview", self.generator.image.copy().convert("RGB"), 0)
        self._render_scene(temp, preserve_scroll=True, draw_overlays=False, fast=True)

    def _finish_generation(self) -> None:
        self.running = False
        self.paused = False
        self.pause_btn.configure(text="Pause", state="disabled")
        self.step_btn.configure(state="disabled")
        p = self.pending
        scene = Scene(
            id=str(uuid.uuid4()), title=p["title"], kind=p["kind"], image=self.generator.image.copy().convert("RGB"), seed=p["seed"],
            parent_id=p["parent_id"], source_rect=p["source_rect"], biome=getattr(self.generator, "biome", p["biome"]),
            semantic=getattr(self.generator, "semantic", p["semantic"]), entities=list(getattr(self.generator, "entities", [])),
            paths=list(getattr(self.generator, "paths", [])), metadata=dict(getattr(self.generator, "metadata", {})),
        )
        self.state.add_scene(scene)
        if p.get("focus_entity_id"):
            self._link_entity_child(p["focus_entity_id"], scene.id)
        self.selection = None
        self.selected_entity_id = None
        self._refresh_tree()
        self.fit_view()
        self._status(f"Complete · {scene.kind} · {scene.biome}", 1.0)

    def _link_entity_child(self, entity_id: str, child_scene_id: str) -> None:
        world_ref = None
        for scene in self.state.scenes.values():
            for entity in scene.entities:
                if entity.id == entity_id:
                    entity.child_scene_id = child_scene_id
                    world_ref = entity.metadata.get("world_ref")
                    origin = entity.metadata.get("origin_entity_id")
                    if origin:
                        self._link_entity_child_direct(origin, child_scene_id)
        if world_ref:
            for scene in self.state.scenes.values():
                for entity in scene.entities:
                    if entity.metadata.get("world_ref") == world_ref:
                        entity.child_scene_id = child_scene_id

    def _link_entity_child_direct(self, entity_id: str, child_scene_id: str) -> None:
        for scene in self.state.scenes.values():
            for entity in scene.entities:
                if entity.id == entity_id:
                    entity.child_scene_id = child_scene_id

    def toggle_pause(self) -> None:
        if not self.running:
            return
        self.paused = not self.paused
        if self.paused:
            self._cancel_tick()
            self.pause_btn.configure(text="Resume")
            self.step_btn.configure(state="normal")
            self.status_var.set("Paused")
        else:
            self.pause_btn.configure(text="Pause")
            self.step_btn.configure(state="disabled")
            self._tick(1)

    def single_step(self) -> None:
        if not self.running or not self.paused or not self.steps:
            return
        try:
            next(self.steps)
            self._preview_generator()
        except StopIteration:
            self._finish_generation()

    def current_scene(self) -> Optional[Scene]:
        return self.state.current()

    def select_mode(self) -> None:
        self.pending_place = None
        self.canvas.configure(cursor="crosshair")
        self.status_var.set("Drag an area. Detail generation will analyze biome, roads, towns, and hierarchy before choosing a generator.")

    def _canvas_to_scene(self, event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x) / max(.0001, self.zoom), self.canvas.canvasy(event.y) / max(.0001, self.zoom)

    def _press(self, event) -> None:
        scene = self.current_scene()
        if not scene or self.running:
            return
        x, y = self._canvas_to_scene(event)
        if self.pending_place:
            kind, value = self.pending_place
            if kind == "npc":
                self._place_npc(value, x, y)
            elif kind == "asset":
                self._place_asset(value, x, y)
            self.pending_place = None
            self.canvas.configure(cursor="crosshair")
            return
        entity = scene.entity_at(x, y, 8 / max(.3, self.zoom))
        if entity and entity.movable:
            self.entity_drag_id = entity.id
            self.selected_entity_id = entity.id
            self.entity_drag_offset = (x - entity.x, y - entity.y)
            self._sync_transform_controls()
            self.render()
            return
        if entity:
            self.selected_entity_id = entity.id
        else:
            self.selected_entity_id = None
        self._sync_transform_controls()
        self.drag_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        if self.drag_item:
            self.canvas.delete(self.drag_item)
        self.drag_item = self.canvas.create_rectangle(*self.drag_start, *self.drag_start, outline="#f2c94c", width=2, dash=(7, 4), tags="selection")

    def _drag(self, event) -> None:
        scene = self.current_scene()
        if not scene:
            return
        if self.entity_drag_id:
            entity = next((e for e in scene.entities if e.id == self.entity_drag_id), None)
            if entity:
                x, y = self._canvas_to_scene(event)
                entity.x = max(0, min(scene.image.width, x - self.entity_drag_offset[0]))
                entity.y = max(0, min(scene.image.height, y - self.entity_drag_offset[1]))
                self._request_drag_render()
            return
        if self.drag_start and self.drag_item:
            x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
            self.canvas.coords(self.drag_item, self.drag_start[0], self.drag_start[1], x, y)

    def _release(self, event) -> None:
        scene = self.current_scene()
        if not scene:
            return
        if self.entity_drag_id:
            self.entity_drag_id = None
            self._sync_transform_controls()
            self.render()
            return
        if not self.drag_start:
            return
        x2, y2 = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x1, y1 = self.drag_start
        self.drag_start = None
        ax, ay = min(x1, x2) / self.zoom, min(y1, y2) / self.zoom
        bx, by = max(x1, x2) / self.zoom, max(y1, y2) / self.zoom
        ax, ay = max(0, ax), max(0, ay)
        bx, by = min(scene.image.width, bx), min(scene.image.height, by)
        if bx - ax < 18 or by - ay < 18:
            self.selection = None
        else:
            self.selection = (int(ax), int(ay), int(bx), int(by))
            context = resolve_selection(scene, self.selection)
            max_w, max_h = self.DETAIL_PRESETS[self.detail_preset.get()]
            out_w, out_h = rect_aspect_dimensions(self.selection, max_w, max_h, minimum=520)
            self.status_var.set(f"Selected {int(bx-ax)}×{int(by-ay)} · {context.semantic}/{context.biome} · output {out_w}×{out_h} with aspect preserved")
        self.render()

    def _double_click(self, event) -> None:
        scene = self.current_scene()
        if not scene or self.running:
            return
        x, y = self._canvas_to_scene(event)
        entity = scene.entity_at(x, y, 12 / max(.3, self.zoom))
        if entity:
            self.enter_entity(entity)
            return
        child = self._child_at(scene, x, y)
        if child:
            self._transition_to(child.id)

    def enter_entity(self, entity: Entity) -> None:
        scene = self.current_scene()
        if not scene:
            return
        if entity.child_scene_id and entity.child_scene_id in self.state.scenes:
            self._transition_to(entity.child_scene_id)
            return
        if entity.kind in {"npc", "custom", "object"}:
            self.selected_entity_id = entity.id
            self._sync_transform_controls()
            self.render()
            return
        context = context_for_entity(scene, entity)
        if context.semantic not in {"town", "castle", "mage_tower", "cave", "building", "room", "ocean", "terrain"}:
            return
        self._generate_context(scene, context, self._entity_rect(scene, entity))

    def _entity_rect(self, scene: Scene, entity: Optional[Entity]) -> tuple[int, int, int, int]:
        if not entity:
            return (scene.image.width//4, scene.image.height//4, scene.image.width*3//4, scene.image.height*3//4)
        pad = max(50, int(max(entity.width, entity.height) * 2.2))
        return (
            max(0, int(entity.x-pad)), max(0, int(entity.y-pad)),
            min(scene.image.width, int(entity.x+pad)), min(scene.image.height, int(entity.y+pad)),
        )

    def _child_at(self, scene: Scene, x: float, y: float) -> Optional[Scene]:
        best = None
        best_area = None
        for child in self.state.scenes.values():
            if child.parent_id != scene.id or not child.source_rect:
                continue
            x1, y1, x2, y2 = child.source_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                area = (x2-x1)*(y2-y1)
                if best is None or area < best_area:
                    best = child
                    best_area = area
        return best

    def _selection_for(self, scene: Scene) -> Optional[tuple[int, int, int, int]]:
        if self.selection:
            x1, y1, x2, y2 = self.selection
            return max(0,x1), max(0,y1), min(scene.image.width,x2), min(scene.image.height,y2)
        return None

    def _wheel(self, event) -> str:
        if event.state & 0x0004:
            return "break"
        scene = self.current_scene()
        if not scene:
            return "break"
        x, y = self._canvas_to_scene(event)
        if event.delta > 0:
            child = self._child_at(scene, x, y)
            entity = scene.entity_at(x, y, 10 / max(.3, self.zoom))
            if self.zoom >= 2.8 and entity and entity.child_scene_id in self.state.scenes:
                self._transition_to(entity.child_scene_id)
                return "break"
            if self.zoom >= 3.2 and child:
                self._transition_to(child.id)
                return "break"
            self.change_zoom(1.16, event.x, event.y)
        elif event.delta < 0:
            if self.zoom <= .2 and scene.parent_id:
                self.go_parent()
                return "break"
            self.change_zoom(.86, event.x, event.y)
        return "break"

    def _wheel_linux(self, event, direction: int) -> str:
        class E: pass
        e = E(); e.x = event.x; e.y = event.y; e.delta = 120 * direction; e.state = 0
        return self._wheel(e)

    def change_zoom(self, factor: float, mouse_x: Optional[int] = None, mouse_y: Optional[int] = None) -> None:
        scene = self.current_scene()
        if not scene:
            return
        old = self.zoom
        new = max(.12, min(4.0, old * factor))
        if abs(new-old) < .001:
            return
        if mouse_x is None:
            mouse_x = max(1, self.canvas.winfo_width()//2)
            mouse_y = max(1, self.canvas.winfo_height()//2)
        world_x = self.canvas.canvasx(mouse_x) / old
        world_y = self.canvas.canvasy(mouse_y) / old
        self.zoom = new
        self.render(fast=True)
        self._schedule_quality_render()
        total_w = scene.image.width * new
        total_h = scene.image.height * new
        left = world_x * new - mouse_x
        top = world_y * new - mouse_y
        if total_w > 0:
            self.canvas.xview_moveto(max(0, min(1, left / total_w)))
        if total_h > 0:
            self.canvas.yview_moveto(max(0, min(1, top / total_h)))

    def fit_view(self) -> None:
        scene = self.current_scene()
        if not scene:
            return
        self.root.update_idletasks()
        cw = max(200, self.canvas.winfo_width()-24)
        ch = max(200, self.canvas.winfo_height()-24)
        self.zoom = max(.12, min(1.0, min(cw/scene.image.width, ch/scene.image.height)))
        self.render()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def _pan_start(self, event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def _pan_move(self, event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def go_parent(self) -> None:
        scene = self.current_scene()
        if scene and scene.parent_id in self.state.scenes:
            parent_id = scene.parent_id
            source = scene.source_rect
            self._transition_to(parent_id, source)

    def _transition_to(self, scene_id: str, highlight_rect=None) -> None:
        if scene_id not in self.state.scenes or scene_id == self.state.current_id:
            return
        old = self.current_scene()
        new = self.state.scenes[scene_id]
        self._animate_transition(old, new, lambda: self._complete_transition(scene_id, highlight_rect))

    def _animate_transition(self, old: Optional[Scene], new: Scene, done) -> None:
        if not old or self.canvas.winfo_width() < 100 or self.canvas.winfo_height() < 100:
            done(); return
        w = max(320, self.canvas.winfo_width())
        h = max(240, self.canvas.winfo_height())
        a = self._fit_to_box(old.image, w, h)
        b = self._fit_to_box(new.image, w, h)
        frames = 7
        self.transition_photos.clear()
        def frame(i: int) -> None:
            if i > frames:
                done(); return
            alpha = i / frames
            blend = Image.blend(a, b, alpha)
            photo = ImageTk.PhotoImage(blend)
            self.transition_photos.append(photo)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, image=photo, anchor="nw")
            self.root.after(28, lambda: frame(i+1))
        frame(0)

    def _fit_to_box(self, image: Image.Image, w: int, h: int) -> Image.Image:
        ratio = min(w/image.width, h/image.height)
        size = (max(1,int(image.width*ratio)), max(1,int(image.height*ratio)))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        out = Image.new("RGB", (w,h), (16,18,22))
        out.paste(resized, ((w-size[0])//2,(h-size[1])//2))
        return out

    def _complete_transition(self, scene_id: str, highlight_rect=None) -> None:
        self.state.current_id = scene_id
        self.selection = highlight_rect
        self.selected_entity_id = None
        self._refresh_tree()
        self.fit_view()
        scene = self.current_scene()
        if scene:
            self.status_var.set(f"{scene.kind.title()} · {scene.biome.replace('_',' ')} · double-click linked places or select another area")

    def _grid_overlay(self, image: Image.Image) -> Image.Image:
        kind = self.grid_type.get()
        if kind == "None":
            return image
        out = image.convert("RGBA")
        d = ImageDraw.Draw(out, "RGBA")
        cell = max(12, int(self.grid_size.get()))
        line = (245, 242, 230, 65)
        if kind == "Square":
            for x in range(0,out.width,cell): d.line((x,0,x,out.height),fill=line,width=1)
            for y in range(0,out.height,cell): d.line((0,y,out.width,y),fill=line,width=1)
        else:
            r=cell/2; hh=math.sqrt(3)*r; col=0; x=r
            while x<out.width+r:
                y=hh/2+(hh/2 if col%2 else 0)
                while y<out.height+hh:
                    pts=[(x+r*math.cos(math.radians(60*i)),y+r*math.sin(math.radians(60*i))) for i in range(6)]
                    d.polygon(pts,outline=line)
                    y+=hh
                x+=1.5*r;col+=1
        return out.convert("RGB")

    def _request_drag_render(self) -> None:
        if self._drag_render_after_id:
            return
        def run() -> None:
            self._drag_render_after_id = None
            self.render(fast=True)
        self._drag_render_after_id = self.root.after(16, run)

    def _invalidate_render_cache(self, scene_id: Optional[str] = None) -> None:
        if scene_id is None:
            self._base_render_cache.clear()
            self._scaled_render_cache.clear()
            return
        self._base_render_cache = {k: v for k, v in self._base_render_cache.items() if not k or k[0] != scene_id}
        self._scaled_render_cache = {k: v for k, v in self._scaled_render_cache.items() if not k or k[0] != scene_id}

    def _schedule_quality_render(self) -> None:
        if self._quality_after_id:
            try:
                self.root.after_cancel(self._quality_after_id)
            except tk.TclError:
                pass
        self._quality_after_id = self.root.after(110, self._quality_render)

    def _quality_render(self) -> None:
        self._quality_after_id = None
        if not self.running:
            scene = self.current_scene()
            if scene:
                self._render_scene(scene, preserve_scroll=True, fast=False)

    def _base_image_for_scene(self, scene: Scene) -> Image.Image:
        if scene.kind == "preview":
            return scene.image
        key = (scene.id, id(scene.image), self.grid_type.get(), int(self.grid_size.get()))
        cached = self._base_render_cache.get(key)
        if cached is not None:
            return cached
        image = self._grid_overlay(scene.image)
        if len(self._base_render_cache) > 12:
            for stale in list(self._base_render_cache)[:4]:
                self._base_render_cache.pop(stale, None)
        self._base_render_cache[key] = image
        return image

    def render(self, fast: bool = False) -> None:
        scene = self.current_scene()
        if not scene:
            self.canvas.delete("all")
            return
        self._render_scene(scene, fast=fast)

    def _render_scene(self, scene: Scene, preserve_scroll: bool = False, draw_overlays: bool = True, fast: bool = False) -> None:
        image = self._base_image_for_scene(scene)
        size = (max(1, int(image.width * self.zoom)), max(1, int(image.height * self.zoom)))
        zoom_key = round(self.zoom, 4)
        key = (scene.id, id(scene.image), self.grid_type.get() if scene.kind != "preview" else "preview", int(self.grid_size.get()) if scene.kind != "preview" else 0, zoom_key, bool(fast))
        preview = self._scaled_render_cache.get(key)
        if preview is None:
            resample = Image.Resampling.BILINEAR if fast else Image.Resampling.LANCZOS
            preview = image.resize(size, resample)
            if scene.kind != "preview":
                if len(self._scaled_render_cache) > 20:
                    for stale in list(self._scaled_render_cache)[:7]:
                        self._scaled_render_cache.pop(stale, None)
                self._scaled_render_cache[key] = preview
        self.photo = ImageTk.PhotoImage(preview)
        xfrac = self.canvas.xview()[0] if preserve_scroll else None
        yfrac = self.canvas.yview()[0] if preserve_scroll else None
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw", tags="map")
        self.canvas.configure(scrollregion=(0, 0, size[0], size[1]))
        self.overlay_photos.clear()
        if draw_overlays:
            self._draw_child_portals(scene)
            self._draw_dynamic_entities(scene)
            if self.selection:
                x1, y1, x2, y2 = self.selection
                self.drag_item = self.canvas.create_rectangle(x1*self.zoom, y1*self.zoom, x2*self.zoom, y2*self.zoom, outline="#f2c94c", width=2, dash=(7, 4), tags="selection")
            else:
                self.drag_item = None
        if preserve_scroll and xfrac is not None:
            self.canvas.xview_moveto(xfrac)
            self.canvas.yview_moveto(yfrac)
        self.zoom_label.configure(text=f"{self.zoom*100:.0f}%")
        self.layer_label.configure(text=f"{scene.title} · {scene.kind} · {scene.image.width}×{scene.image.height}")
        lod = scene.metadata.get("lod")
        lod_text = f" · LOD {lod}" if lod is not None else ""
        self.context_label.configure(text=f"{scene.kind.title()} · {scene.biome.replace('_',' ').title()} · level {scene.level}{lod_text}")

    def _draw_child_portals(self, scene: Scene) -> None:
        for child in self.state.scenes.values():
            if child.parent_id != scene.id or not child.source_rect:
                continue
            x1,y1,x2,y2=child.source_rect
            self.canvas.create_rectangle(x1*self.zoom,y1*self.zoom,x2*self.zoom,y2*self.zoom,outline="#67b7ff",width=2,dash=(5,4),tags="portal")
            if self.zoom > .45:
                self.canvas.create_text((x1+5)*self.zoom,(y1+5)*self.zoom,text=f"↳ {child.title}",anchor="nw",fill="#d9efff",font=("Segoe UI",9,"bold"))

    def _draw_dynamic_entities(self, scene: Scene) -> None:
        for entity in scene.entities:
            if entity.kind == "npc":
                if self.zoom < .30 and not entity.metadata.get("manual") and entity.id != self.selected_entity_id:
                    continue
                r = max(4, int(entity.width * self.zoom / 2))
                x, y = entity.x * self.zoom, entity.y * self.zoom
                color = self.NPC_COLORS.get(entity.name, self.NPC_COLORS.get(entity.subtype.title(), "#d3a35d"))
                self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=color, outline="#1a1c20", width=2, tags=("entity", entity.id))
                if self.zoom > .75:
                    self.canvas.create_text(x, y+r+8, text=entity.name, fill="#f1f3f7", font=("Segoe UI", 8), tags=("entity", entity.id))
            elif entity.kind == "custom" and entity.asset_id:
                tw = max(5, int(entity.width * self.zoom))
                th = max(5, int(entity.height * self.zoom))
                sprite = self.assets.get_scaled(
                    entity.asset_id,
                    tw,
                    th,
                    int(entity.metadata.get("rotation", 0)),
                    bool(entity.metadata.get("flip_h", False)),
                    bool(entity.metadata.get("flip_v", False)),
                )
                if sprite:
                    photo = ImageTk.PhotoImage(sprite)
                    self.overlay_photos.append(photo)
                    self.canvas.create_image(entity.x*self.zoom, entity.y*self.zoom, image=photo, anchor="center", tags=("entity", entity.id))
            elif self.zoom >= 1.25 and entity.kind in {"settlement", "landmark"}:
                self._draw_semantic_marker_canvas(entity)
            elif self.zoom >= 1.75 and entity.kind == "building" and scene.kind in {"town", "castle", "mage_tower", "region"}:
                self._draw_semantic_marker_canvas(entity)
            if entity.id == self.selected_entity_id:
                x1 = (entity.x - entity.width/2) * self.zoom
                y1 = (entity.y - entity.height/2) * self.zoom
                x2 = (entity.x + entity.width/2) * self.zoom
                y2 = (entity.y + entity.height/2) * self.zoom
                self.canvas.create_rectangle(x1, y1, x2, y2, outline="#ffde59", width=2, dash=(4, 3))

    def _draw_semantic_marker_canvas(self, entity: Entity) -> None:
        x, y = entity.x*self.zoom, entity.y*self.zoom
        scale = max(.8, min(2.2, self.zoom))
        kind = entity.subtype
        ink = "#302820"
        if entity.kind == "settlement":
            r = max(8, int(11*scale))
            self.canvas.create_oval(x-r, y-r, x+r, y+r, fill="#d7c28d", outline=ink, width=2, tags=("entity", entity.id))
            if kind == "castle":
                self.canvas.create_rectangle(x-r*.65, y-r*.6, x+r*.65, y+r*.55, fill="#9b978d", outline=ink, width=2, tags=("entity", entity.id))
        elif kind in {"wizard_tower", "mage_tower"}:
            r = max(9, int(12*scale))
            self.canvas.create_oval(x-r*.65, y-r, x+r*.65, y+r, fill="#8d8582", outline=ink, width=2, tags=("entity", entity.id))
            self.canvas.create_polygon(x-r, y-r*.7, x, y-r*1.8, x+r, y-r*.7, fill="#5f4a75", outline=ink, tags=("entity", entity.id))
        elif kind in {"cave", "cave_mouth", "dungeon_entrance", "mine"}:
            r = max(9, int(12*scale))
            self.canvas.create_arc(x-r, y-r*.6, x+r, y+r, start=0, extent=180, style="arc", outline="#403a34", width=max(3, int(4*scale)), tags=("entity", entity.id))
        elif kind in {"castle", "fort"}:
            r = max(10, int(13*scale))
            self.canvas.create_rectangle(x-r, y-r*.75, x+r, y+r*.75, fill="#969187", outline=ink, width=2, tags=("entity", entity.id))
        elif entity.kind == "building":
            w = max(12, entity.width*self.zoom*.8)
            h = max(10, entity.height*self.zoom*.8)
            self.canvas.create_rectangle(x-w/2, y-h/2, x+w/2, y+h/2, fill="#c7b38b", outline=ink, width=2, tags=("entity", entity.id))
        else:
            r = max(7, int(9*scale))
            self.canvas.create_oval(x-r, y-r, x+r, y+r, fill="#a98a63", outline=ink, width=2, tags=("entity", entity.id))
        if self.zoom >= 1.65 and entity.name:
            self.canvas.create_text(x, y+max(18, entity.height*self.zoom*.55)+7, text=entity.name, fill="#f0eadc", font=("Segoe UI", 8, "bold"), tags=("entity", entity.id))

    def _draw_npc_palette(self) -> None:
        self.npc_palette.delete("all")
        for i,name in enumerate(self.NPC_TYPES):
            y=22+i*36
            self.npc_palette.create_oval(10,y-10,30,y+10,fill=self.NPC_COLORS[name],outline="#0f1115",width=2,tags=(f"npc:{name}",))
            self.npc_palette.create_text(40,y,text=name,anchor="w",fill="#e9ebef",font=("Segoe UI",10),tags=(f"npc:{name}",))

    def _npc_palette_press(self, event) -> None:
        item = self.npc_palette.find_closest(event.x,event.y)
        if not item:return
        tags=self.npc_palette.gettags(item[0])
        tag=next((t for t in tags if t.startswith("npc:")),None)
        if tag:self._palette_drag_name=tag.split(":",1)[1]

    def _npc_palette_release(self, _event) -> None:
        name=getattr(self,"_palette_drag_name",None)
        if not name:return
        self._palette_drag_name=None
        px,py=self.root.winfo_pointerx(),self.root.winfo_pointery()
        rx,ry=self.canvas.winfo_rootx(),self.canvas.winfo_rooty()
        if rx<=px<=rx+self.canvas.winfo_width() and ry<=py<=ry+self.canvas.winfo_height():
            class E: pass
            e=E();e.x=px-rx;e.y=py-ry
            x,y=self._canvas_to_scene(e)
            self._place_npc(name,x,y)

    def _arm_place(self, kind: str, value: str) -> None:
        if not self.current_scene():return
        self.pending_place=(kind,value);self.canvas.configure(cursor="plus");self.status_var.set(f"Click the map to place {value}")

    def _place_npc(self, name: str, x: float, y: float) -> None:
        scene=self.current_scene()
        if not scene:return
        entity = Entity(str(uuid.uuid4()), "npc", name.lower(), name, x, y, 28, 28, movable=True, metadata={"biome": scene.sample_biome(x, y), "manual": True, "initial_width": 28, "initial_height": 28, "rotation": 0, "flip_h": False, "flip_v": False, "keep_aspect": True})
        scene.entities.append(entity)
        self.selected_entity_id = entity.id
        self._sync_transform_controls()
        self.render()

    def import_png(self) -> None:
        path=filedialog.askopenfilename(filetypes=[("PNG image","*.png")])
        if not path:return
        try:asset=self.assets.import_png(path)
        except Exception as exc:
            messagebox.showerror("Import failed",str(exc));return
        self._refresh_assets();self.asset_list.selection_clear(0,tk.END);self.asset_list.selection_set(tk.END);self._asset_selected()

    def _refresh_assets(self) -> None:
        self.asset_list.delete(0,tk.END)
        self._asset_ids=[]
        for asset in self.assets.assets.values():
            self._asset_ids.append(asset.id);self.asset_list.insert(tk.END,asset.name)

    def _selected_asset_id(self) -> Optional[str]:
        sel=self.asset_list.curselection()
        if not sel:return None
        idx=sel[0]
        return self._asset_ids[idx] if idx<len(getattr(self,"_asset_ids",[])) else None

    def _asset_selected(self, _event=None) -> None:
        aid = self._selected_asset_id()
        img = self.assets.get(aid)
        if not img or not aid:
            self.asset_preview.configure(image="", text="No asset selected")
            return
        asset = self.assets.assets[aid]
        self.asset_pixelation.set(asset.pixelation)
        thumb = img.copy()
        thumb.thumbnail((220, 120), Image.Resampling.NEAREST if asset.pixelation > 1 else Image.Resampling.LANCZOS)
        self.asset_preview_photo = ImageTk.PhotoImage(thumb)
        self.asset_preview.configure(image=self.asset_preview_photo, text="")

    def rename_selected_asset(self) -> None:
        aid = self._selected_asset_id()
        if not aid:
            return
        asset = self.assets.assets[aid]
        name = simpledialog.askstring("Rename asset", "Asset name:", initialvalue=asset.name, parent=self.root)
        if name and self.assets.rename(aid, name):
            self._refresh_assets()
            try:
                idx = self._asset_ids.index(aid)
                self.asset_list.selection_set(idx)
                self.asset_list.see(idx)
            except ValueError:
                pass
            self._asset_selected()

    def apply_asset_pixelation(self) -> None:
        aid = self._selected_asset_id()
        if not aid:
            return
        self.assets.set_pixelation(aid, int(self.asset_pixelation.get()))
        self._asset_selected()
        self.render()

    def reset_asset_pixelation(self) -> None:
        aid = self._selected_asset_id()
        if not aid:
            return
        self.assets.reset_pixelation(aid)
        self.asset_pixelation.set(1)
        self._asset_selected()
        self.render()

    def arm_selected_asset(self) -> None:
        aid = self._selected_asset_id()
        if aid:
            self._arm_place("asset", aid)

    def _place_asset(self, aid: str, x: float, y: float) -> None:
        scene = self.current_scene()
        img = self.assets.get(aid)
        if not scene or not img:
            return
        scale = min(1.0, 96 / max(img.width, img.height))
        w = max(24, img.width * scale)
        h = max(24, img.height * scale)
        asset = self.assets.assets[aid]
        entity = Entity(
            str(uuid.uuid4()), "custom", "png", asset.name, x, y, w, h, movable=True, asset_id=aid,
            metadata={
                "manual": True,
                "biome": scene.sample_biome(x, y),
                "rotation": 0,
                "flip_h": False,
                "flip_v": False,
                "initial_width": w,
                "initial_height": h,
                "keep_aspect": True,
            },
        )
        scene.entities.append(entity)
        self.selected_entity_id = entity.id
        self._sync_transform_controls()
        self.render()

    def _texture_slot_changed(self, _event=None) -> None:
        aid = self.assets.slots.get(self.texture_slot.get())
        if aid and aid in self.assets.assets:
            self.texture_status.configure(text=f"{self.texture_slot.get()} → {self.assets.assets[aid].name}")
        else:
            self.texture_status.configure(text="Built-in texture")

    def assign_texture(self) -> None:
        aid = self._selected_asset_id()
        if not aid:
            messagebox.showinfo("Select an asset", "Choose an imported PNG first.")
            return
        slot = self.texture_slot.get()
        self.assets.assign(slot, aid)
        self.texture_status.configure(text=f"{slot} → {self.assets.assets[aid].name}")

    def _selected_movable(self) -> Optional[Entity]:
        scene = self.current_scene()
        if not scene or not self.selected_entity_id:
            return None
        return next((e for e in scene.entities if e.id == self.selected_entity_id and e.movable), None)

    def _sync_transform_controls(self) -> None:
        entity = self._selected_movable()
        if not entity:
            if hasattr(self, "transform_status"):
                self.transform_status.configure(text="No movable object selected")
            return
        self.transform_w.set(round(entity.width, 1))
        self.transform_h.set(round(entity.height, 1))
        self.keep_aspect.set(bool(entity.metadata.get("keep_aspect", True)))
        rotation = int(entity.metadata.get("rotation", 0)) % 360
        if hasattr(self, "transform_status"):
            self.transform_status.configure(text=f"{entity.name} · {entity.width:.0f}×{entity.height:.0f} · {rotation}°")

    def resize_selected(self, factor: float) -> None:
        scene = self.current_scene()
        entity = self._selected_movable()
        if not scene or not entity:
            return
        entity.width = max(6, min(scene.image.width * 2, entity.width * factor))
        entity.height = max(6, min(scene.image.height * 2, entity.height * factor))
        entity.metadata["keep_aspect"] = True
        self._sync_transform_controls()
        self.render()

    def apply_transform_size(self) -> None:
        scene = self.current_scene()
        entity = self._selected_movable()
        if not scene or not entity:
            return
        try:
            width = max(4.0, min(scene.image.width * 2.0, float(self.transform_w.get())))
            height = max(4.0, min(scene.image.height * 2.0, float(self.transform_h.get())))
        except (TypeError, ValueError, tk.TclError):
            return
        keep = bool(self.keep_aspect.get())
        if keep:
            aspect = entity.width / max(1.0, entity.height)
            height = width / max(.001, aspect)
        entity.width = width
        entity.height = height
        entity.metadata["keep_aspect"] = keep
        self._sync_transform_controls()
        self.render()

    def rotate_selected(self, degrees: int) -> None:
        entity = self._selected_movable()
        if not entity:
            return
        entity.metadata["rotation"] = (int(entity.metadata.get("rotation", 0)) + int(degrees)) % 360
        self._sync_transform_controls()
        self.render()

    def flip_selected(self, axis: str) -> None:
        entity = self._selected_movable()
        if not entity:
            return
        key = "flip_h" if axis == "h" else "flip_v"
        entity.metadata[key] = not bool(entity.metadata.get(key, False))
        self._sync_transform_controls()
        self.render()

    def reset_selected_transform(self) -> None:
        entity = self._selected_movable()
        if not entity:
            return
        entity.width = float(entity.metadata.get("initial_width", 28 if entity.kind == "npc" else entity.width))
        entity.height = float(entity.metadata.get("initial_height", 28 if entity.kind == "npc" else entity.height))
        entity.metadata["rotation"] = 0
        entity.metadata["flip_h"] = False
        entity.metadata["flip_v"] = False
        entity.metadata["keep_aspect"] = True
        self._sync_transform_controls()
        self.render()

    def _resize_entity_wheel(self, event) -> str:
        scene = self.current_scene()
        if not scene:
            return "break"
        x, y = self._canvas_to_scene(event)
        entity = scene.entity_at(x, y, 8 / max(.3, self.zoom))
        if entity and entity.movable:
            self.selected_entity_id = entity.id
            self.resize_selected(1.12 if event.delta > 0 else .89)
        return "break"

    def delete_selected_entity(self) -> None:
        scene = self.current_scene()
        if not scene or not self.selected_entity_id:
            return
        scene.entities = [e for e in scene.entities if e.id != self.selected_entity_id]
        self.selected_entity_id = None
        self._sync_transform_controls()
        self.render()

    def _refresh_tree(self) -> None:
        self.scene_tree.delete(*self.scene_tree.get_children())
        inserted=set()
        def insert_scene(scene_id,parent_node=""):
            if scene_id in inserted or scene_id not in self.state.scenes:return
            scene=self.state.scenes[scene_id]
            node=self.scene_tree.insert(parent_node,"end",iid=scene.id,text=f"{scene.kind.title()} · {scene.title}",open=True)
            inserted.add(scene.id)
            for child_id in self.state.order:
                child=self.state.scenes.get(child_id)
                if child and child.parent_id==scene.id:insert_scene(child_id,node)
        for sid in self.state.order:
            scene=self.state.scenes.get(sid)
            if scene and not scene.parent_id:insert_scene(sid)
        if self.state.current_id in self.state.scenes:
            try:self.scene_tree.selection_set(self.state.current_id);self.scene_tree.see(self.state.current_id)
            except tk.TclError:pass

    def _tree_open(self,_event=None)->None:
        sel=self.scene_tree.selection()
        if sel and sel[0] in self.state.scenes:self._transition_to(sel[0])

    def save_project(self) -> None:
        if not self.state.scenes:return
        path=filedialog.asksaveasfilename(defaultextension=".cforge",filetypes=[("CampaignForge project","*.cforge")],initialfile="campaign.cforge")
        if not path:return
        try:save_campaign(path,self.state,self.assets);self.status_var.set(f"Saved project: {Path(path).name}")
        except Exception as exc:messagebox.showerror("Save failed",str(exc))

    def open_project(self) -> None:
        path=filedialog.askopenfilename(filetypes=[("CampaignForge project","*.cforge"),("ZIP archive","*.zip")])
        if not path:return
        try:
            state,assets,tmp=load_campaign(path)
            self.state=state;self.assets=assets;self.project_extract_dir=tmp
            self._invalidate_render_cache()
            for key,var in self.rule_vars.items():var.set(getattr(self.state.config,key))
            self._refresh_assets();self.selection=None;self.selected_entity_id=None;self._refresh_tree();self.fit_view();self.status_var.set(f"Opened {Path(path).name}")
        except Exception as exc:messagebox.showerror("Open failed",str(exc))

    def rename_scene(self) -> None:
        scene=self.current_scene()
        if not scene:return
        name=simpledialog.askstring("Rename scene","Scene name:",initialvalue=scene.title,parent=self.root)
        if name and name.strip():scene.title=name.strip();self._refresh_tree();self.render()

    def delete_selected_scene(self) -> None:
        selected = self.scene_tree.selection()
        scene_id = selected[0] if selected and selected[0] in self.state.scenes else self.state.current_id
        scene = self.state.scenes.get(scene_id) if scene_id else None
        if not scene:
            return
        if scene.parent_id is None:
            messagebox.showinfo("Cannot delete world", "The root world cannot be deleted as a sub-area. Create a new world instead.")
            return
        descendants = self.state.descendants(scene.id)
        suffix = f" and its {len(descendants)} generated child area(s)" if descendants else ""
        if not messagebox.askyesno("Delete generated sub-area", f"Delete '{scene.title}'{suffix}?\n\nIts parent and unrelated areas will be kept."):
            return
        source_rect = scene.source_rect
        was_current = self.state.current_id in {scene.id, *descendants}
        removed = self.state.remove_scene(scene.id, include_descendants=True)
        self._invalidate_render_cache()
        self.selected_entity_id = None
        self._refresh_tree()
        if was_current:
            self.selection = source_rect
            self.fit_view()
        else:
            self.render()
        self.status_var.set(f"Deleted {len(removed)} generated area(s); parent content was preserved")

    def export_png(self) -> None:
        scene=self.current_scene()
        if not scene:return
        path=filedialog.asksaveasfilename(defaultextension=".png",filetypes=[("PNG image","*.png")],initialfile=self._safe(scene.title)+".png")
        if not path:return
        self._compose_scene(scene).save(path,"PNG");self.status_var.set(f"Exported {Path(path).name}")

    def export_pack(self) -> None:
        if not self.state.scenes:return
        path=filedialog.asksaveasfilename(defaultextension=".zip",filetypes=[("ZIP archive","*.zip")],initialfile="campaign_pack.zip")
        if not path:return
        manifest={"format":"CampaignForge Campaign Pack","version":1,"scenes":[]}
        with tempfile.TemporaryDirectory(prefix="campaignforge_pack_") as tmpdir:
            tmp=Path(tmpdir)
            with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as z:
                for i,sid in enumerate(self.state.order):
                    scene=self.state.scenes[sid];name=f"maps/{i+1:03d}_{scene.kind}_{self._safe(scene.title)}.png";file=tmp/f"{sid}.png";self._compose_scene(scene).save(file,"PNG");z.write(file,name)
                    manifest["scenes"].append({"id":scene.id,"title":scene.title,"kind":scene.kind,"parent_id":scene.parent_id,"source_rect":scene.source_rect,"image":name,"entities":[e.to_dict() for e in scene.entities]})
                z.writestr("campaign.json",json.dumps(manifest,indent=2))
        self.status_var.set(f"Exported pack: {Path(path).name}")

    def _compose_scene(self, scene: Scene) -> Image.Image:
        out=self._grid_overlay(scene.image).convert("RGBA");d=ImageDraw.Draw(out,"RGBA")
        for e in scene.entities:
            if e.kind=="npc":
                r=max(5,int(e.width/2));color=self.NPC_COLORS.get(e.name,self.NPC_COLORS.get(e.subtype.title(),"#d3a35d"));rgb=self.root.winfo_rgb(color);fill=tuple(v//256 for v in rgb)+(255,);d.ellipse((e.x-r,e.y-r,e.x+r,e.y+r),fill=fill,outline=(25,27,31,255),width=2)
            elif e.kind=="custom" and e.asset_id:
                sprite = self.assets.get_scaled(
                    e.asset_id,
                    max(1, int(e.width)),
                    max(1, int(e.height)),
                    int(e.metadata.get("rotation", 0)),
                    bool(e.metadata.get("flip_h", False)),
                    bool(e.metadata.get("flip_v", False)),
                )
                if sprite:
                    out.alpha_composite(sprite, (int(e.x-sprite.width/2), int(e.y-sprite.height/2)))
        return out.convert("RGB")

    def _safe(self,value:str)->str:
        return "".join(c.lower() if c.isalnum() else "_" for c in value).strip("_")[:80]


def main() -> None:
    root=tk.Tk()
    CampaignForgeApp(root)
    root.mainloop()
