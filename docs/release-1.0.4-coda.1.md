# Blender Sith Coda 1.0.4 — radius and native pivots

Release candidate v1.0.4-coda.1. Package validation and publication are pending.

The automatic radius is derived from assembled exported rest geometry instead
of half the local bounding-box diagonal. Native pivots survive new 3DO imports
and re-export, preserving attachment matrices as well as visible surfaces.
Explicit authored radius and PivotConstraint overrides remain available.

Red's saved build reproduced the radius failure (0.081336 versus a native
geometry bound of 0.120722308). Her 0.133282 workaround remains conservative.
An archived hand pivot explains a 0.010957468 JKU attachment-origin shift.
Coda independently checked these reports and implemented the fixes. No private
model, image, game asset or conversation is bundled; regressions use synthetic
fixtures. Existing saved blends that already lost pivots need fresh source import
or verified rig repair. Background tests do not establish in-game or multiplayer
acceptance; Blender scene import still exposes geoset 0 only.
