# CampaignForge 1.0 Architecture

CampaignForge represents a campaign as a persistent hierarchy of semantic scenes. A scene is not just an image: it contains entities, paths, biome data, parent/child relationships, and generation metadata.

## Hierarchy

`World → Region/Terrain → Town/Castle/Cave/Mage Tower → Building → Room`

A deeper scene may only generate content that makes sense at its hierarchy level. Zooming reveals detail; it does not invent a new unrelated world.

## Stable location identity

Major world locations carry a stable `world_ref`. When a region is generated, every selected major location is cloned into child coordinates while retaining that identity and an `origin_entity_id`.

This lets a town remain the same town across several views. If its detailed scene already exists, cloned representations can link back to that same child scene rather than creating a duplicate.

## Selection context and LOD

`campaignforge/context.py` resolves a selection before generation starts.

Inputs include:

- scene kind / hierarchy level
- selection area as a fraction of the parent
- dominant biome
- every major location inside the selection
- roads near or connected to those locations
- buildings/rooms allowed at the current level

The resolver assigns a semantic generator and an LOD value:

- **LOD 0** — overview
- **LOD 1** — regional / multi-location
- **LOD 2** — local terrain
- **LOD 3** — detailed location

A selection containing two or more major locations is always treated as an area-level selection. It cannot collapse into a single city generator.

## Multi-location transform

Regional detail generation uses an affine mapping from the source rectangle into the child scene. Because child dimensions preserve the source aspect ratio, X and Y use the same effective scale.

Every selected location therefore preserves:

- relative X/Y position
- approximate distance
- location subtype
- persistent identity
- child-scene link if one exists

The generator then adds only LOD-appropriate supporting detail.

## Road continuity

Roads and trails are semantic paths rather than decorations.

Major paths may carry:

- `from_ref`
- `to_ref`
- `major`
- stable path ID
- ordered path points

When an area is expanded, parent polylines are clipped to the selected rectangle and mapped into child coordinates. Crossing roads are retained even when both original endpoints are outside the selected rectangle.

When entering a town/castle/mage tower/cave, the resolver derives the approach angle of inherited roads/trails. Detailed generators use those angles as map entrances.

This prevents a two-road regional town from becoming a six-spoke detailed town.

## Major location generators

First-class local generators include:

- `TownGenerator`
- `CastleGenerator`
- `CaveGenerator`
- `MageTowerGenerator`

They all consume parent context and inherited approach directions rather than generating isolated layouts.

## Biome rules

`campaignforge/biomes.py` owns shared terrain compatibility rules. Structures are selected from biome-appropriate pools. Water locations do not receive land-only landmarks, and caves/towers/forts use compatible terrain constraints.

## Aspect ratio

Detail maps are generated from semantic parent data, not by enlarging a screenshot crop. Output dimensions are calculated from the selected rectangle's aspect ratio. This avoids distorted roads, buildings, text, and terrain.

## Generated-scene deletion

`CampaignState.remove_scene()` removes a requested generated branch without removing its parent or unrelated siblings.

The operation:

1. refuses deletion of a root world
2. collects descendants of the selected sub-area
3. removes only that branch
4. clears entity links pointing at removed scenes
5. returns navigation to the surviving parent when necessary

## Asset pipeline

Imported PNG assets retain their original file source. Pixelation is a reversible display/edit property and is never destructively written over the source image.

`AssetLibrary` caches:

- processed originals
- scaled/rotated/flipped variants

Placed assets store transform data on their entity:

- width/height
- rotation
- horizontal/vertical flip
- aspect-lock preference

That metadata is saved in `.cforge` projects.

## Rendering performance

The Tkinter editor separates the static scene image from dynamic overlays.

Performance techniques include:

- base/grid image cache
- zoomed image cache
- bilinear temporary scaling during active interaction
- deferred Lanczos quality pass
- 16 ms throttling for drag redraws
- canvas-native panning
- dynamic-entity LOD at distant zooms
- high-zoom vector overlays for semantic location markers

The generator does not regenerate a child scene just because the user pans or zooms.

## Persistence

`.cforge` files are ZIP containers containing scene PNGs, campaign metadata, entities, paths, hierarchy links, generation rules, imported assets, and asset assignments.

Persistent IDs and child links are what allow the same world location to survive navigation, editing, saving, and reopening.
