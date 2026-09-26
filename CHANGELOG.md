# Changelog

## 1.1.0

### World continuity
- Preserved inherited external road counts in settlement detail views.
- Added persistent building entrance orientation and source geometry.
- Added multi-floor building sections and selectable floor scenes.
- Added bounded detail-selection geometry to prevent extreme stretched views.
- Added persistent Experimental-view camera state to project saves.

### Location generation
- Added dedicated fort, bandit camp, dragon lair, deep cavern, and ruined-settlement generators.
- Prevented recursive cave generation after the terminal cavern level.
- Added large multi-cell dragons to dragon lairs.
- Expanded town building variety and reduced random wilderness housing.
- Added more exterior settlement objects and decorations.

### Roads and terrain
- Made settlement connection graphs optional and distance/importance aware.
- Kept weighted terrain-aware A* road routing.
- Limited long landmark connections.
- Added moisture to persisted world settings.
- Removed redundant mountain glyphs in favor of terrain/elevation rendering.

### Interiors and editor
- Converted generated furniture to persistent movable objects.
- Added building-specific furniture pools and visible object rotation.
- Added immediate grid snapping when the grid is enabled.
- Added larger-entity grid footprint metadata.
- Added side-button map zoom support.
- Added dynamic replacement-asset rendering for supported semantic objects.

### Experimental view
- Added an initial same-world 2.5D renderer with tilt, rotation, elevation exaggeration, terrain height, roads, rivers, structure height, and large-creature scale.
- Experimental mode reads existing `Scene` data and does not regenerate the world.
