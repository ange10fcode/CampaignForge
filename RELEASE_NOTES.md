# CampaignForge 1.1.0

This update focuses on world continuity rather than adding unrelated random content.

The largest changes are exact parent-road inheritance, persistent building/floor geometry, dedicated generators for special locations, movable furniture, less crowded wilderness, and the first same-world Experimental 2.5D view.

The 2.5D renderer is intentionally an initial lightweight implementation. It provides tilt, rotation, elevation, and extruded scene objects while sharing the normal map's persistent world data. Normal 2D remains the primary editing view.

Project saves remain compatible with the current `.cforge` persistence model and now also retain Experimental view/camera state.

Automated tests cover the existing generation/LOD/save systems plus the new 1.1.0 continuity and special-location behavior.
