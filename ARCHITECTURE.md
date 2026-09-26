# CampaignForge 1.1.0 Architecture

## Persistent scene graph

CampaignForge stores a campaign as a graph of `Scene` objects. A generated child scene records:

- its parent scene ID
- source rectangle in the parent
- semantic type
- biome
- seed
- entities
- paths
- generation metadata

Entities can keep a `child_scene_id`, allowing a visible town/building/floor on one map to resolve to the already-generated detailed scene instead of generating a new unrelated one.

## Semantic hierarchy

Generation is selected from context rather than from a random generator list:

```text
World
  Region / Biome
    Settlement / Special Location
      Building
        Floor
          Room
            Furniture / Object
```

`context.py` resolves the semantic meaning of an entity/selection before `app.py` chooses a generator.

## Road continuity

World roads are persistent polylines with endpoint references. Region generation clips and remaps parent paths through the selected rectangle. Entering a settlement derives road-entry angles from those inherited paths. The town generator creates exactly those major external entrances and can add local streets without inventing extra parent roads.

## Building geometry

Town buildings store width, height, entrance angle, building type, and floor count. Interiors preserve the source aspect ratio inside a bounded canvas. The exterior door is placed on the wall facing the inherited entrance direction. Multi-floor buildings first generate a section scene containing persistent floor entities.

## Terminal locations

Caves have a bounded depth:

```text
Cave exterior → Deep cavern → terminal
```

Dragon lairs are also terminal detailed locations. Terminal underground scenes do not recursively generate more cave scenes.

## World environment

The world engine stores elevation, moisture, and temperature grids. Biomes and automatic rivers derive from these fields. Road routing reads the same terrain grid, so geography influences both visual terrain and path cost.

## Two views, one world

Normal 2D and Experimental 2.5D both consume the same `Scene`.

```text
Persistent Scene Data
       /       \
 Normal 2D   Experimental 2.5D
```

`experimental.py` is a renderer, not a generator. It projects the scene's terrain grid, paths, and entities into a lightweight tilted view. Switching modes never creates new locations.

## Persistence

`.cforge` files persist scenes, entities, metadata, paths, configuration, imported assets, texture slots, current scene, and UI view state. Local saves and autosave use the same persistence format.
