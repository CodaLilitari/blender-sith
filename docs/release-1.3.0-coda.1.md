# Blender Sith (Coda) 1.3.0

Red developed these tools while working on rigid-piece JK and MotS characters. This release brings her model and animation audits to the Blender sidebar, Python and the optional MCP companion.

- **Before exporting a character**, check native vertex, UV-vertex and face counts per mesh and per detail level. Inspect stray geometry near cuts and gaps between rigid parts in selected KEY poses. The joint reports identify the directly animated nodes and the additional joints moved by their parents. [Audit guide](audits/README.md)
- **When an animation looks wrong**, check track binding, Euler jumps, joint offsets, priority, foot contact and camera visibility. The reseat pass can write corrected copies of the KEYs that shift joints off their seats. Review each result against the original animation. [Character workflow](modeling-workflow.md)
- **When attaching an object**, optional viewport helpers mark native 3DO pivots and follow the joint frames. Their offsets are static. Blender protects attached preview objects when removing a helper. [Pivot workflow](blender-pipeline.md)
- **At export**, select retail limit checks and detail levels, indexed dithering and fullbright avoidance, or paired 8-bit indexed and 16-bit RGB565 editions. The Blender importer reads the first geoset. The detail-level option duplicates that geoset up to four times. [Usage guide](../USAGE.md)
- **For scripted work**, the matching source includes a 13-tool MCP companion. The extension ZIP contains the underlying format and audit code. [MCP setup and examples](MCP.md)

Download the ZIP for Windows x64, Linux x64 or Apple Silicon macOS, compare its SHA-256 to `SHA256SUMS.txt`, then choose **Install from Disk** in Blender’s add-on preferences. A universal extension ZIP is also available. The release includes per-package receipts, validation results and a build report. [GitHub downloads](https://github.com/CodaLilitari/blender-sith/releases/tag/v1.3.0-coda.1) · [GitLab downloads](https://gitlab.com/codalilitari/blender-sith/-/releases/v1.3.0-coda.1)

Crt Vavros (smlu) and the upstream Blender-Sith contributors created the original toolkit. Red (Claude) authored the character audits, export improvements and viewport pivot helpers. Coda integrated and extended the tools with Andrei. The fork retains the upstream GPL-3.0-or-later license. [Fork history](../FORK.md)
