# Alignment with Blender and Blender Lab MCP

Source review: **2026-11-01**. Blender-Sith is an add-on for Blender, not a
replacement Blender executable. Blender's modeling, rendering, animation,
baking and general scene tools remain available when the add-on is installed.
The extension adds Sith-format import/export behavior and its specific fixes.

## Official MCP already exists

Blender publishes an official **Blender Lab MCP** project. It is a separate
server and Blender add-on, requiring Blender 5.1 or newer; MCP connectivity is
not built into the Blender core installation. Its official landing page and
repository are [Blender Lab MCP](https://www.blender.org/lab/mcp-server/) and
[lab/blender_mcp](https://projects.blender.org/lab/blender_mcp). This is distinct
from community projects with similar names.

This review inspected official source at
[`dbbf836ad4b1025f14a2b3b504c43903f39e0b04`](https://projects.blender.org/lab/blender_mcp/commit/dbbf836ad4b1025f14a2b3b504c43903f39e0b04).
The [official tool inventory](https://projects.blender.org/lab/blender_mcp/src/commit/dbbf836ad4b1025f14a2b3b504c43903f39e0b04/readme_tools.rst)
already covers general Python execution, scene and object summaries,
missing-file and linked-library inspection, API/manual search, screenshots,
viewport navigation, and rendering. Several file-summary tools also have
background-process variants. Blender-Sith's companion should not duplicate
that general-purpose tool collection.

| Need | Appropriate implementation |
| --- | --- |
| General Blender editing, node work, scene inspection and rendering | Blender itself; official Blender Lab MCP when an MCP interface is desired |
| Blender API/manual lookup and interactive visual feedback | Official Blender Lab MCP documentation, screenshot and navigation tools |
| Parse a Sith 3DO with the maintained importer | Blender-Sith `inspect_3do` |
| Convert an authorized 3DO into a new geometry-only `.blend` | Blender-Sith `convert_3do_to_blend` |
| Run exact synthetic 3DO/KEY/MAT checks and retain hash receipts | Blender-Sith `run_regression` |
| Produce the fork's reproducible extension archive and receipt | Blender-Sith `build_extension`, with Blender's official validator as the compatibility authority |

The two MCP servers can be configured as separate entries in an MCP host.
That is an integration arrangement, not a tested automatic bridge: this fork
does not install the official add-on, open its socket, forward calls into it,
or claim an end-to-end coexistence test. The Sith companion creates fresh
jobs rather than attaching to an artist's existing Blender session.

## Keep dependency environments separate

At the inspected official revision,
[`mcp/pyproject.toml`](https://projects.blender.org/lab/blender_mcp/src/commit/dbbf836ad4b1025f14a2b3b504c43903f39e0b04/mcp/pyproject.toml)
declares `blender-mcp` version 1.0.2 and an MCP SDK dependency below version 2.
This fork's companion pins MCP SDK 2.3.0. Install each server in its own
external environment and point the host at its own executable. Do not combine
their requirements in one environment. Their server version numbers are
independent from the Blender-Sith add-on version.

The official project discovers tool modules internally, but that implementation
detail is not a stable third-party plugin API. Its
[design discussion](https://projects.blender.org/blender/blender/issues/152011)
also considers separate MCP servers for customizations. A direct backend adapter
would need its own implementation and integration tests; none is implied here.

## Reuse Blender's existing commands

Blender already supplies `extension build`, `extension validate`, installation
commands, repository management, and platform-split builds. See the
[official extension command reference](https://docs.blender.org/manual/en/5.2/advanced/command_line/extension_arguments.html).
The fork's packager adds deterministic bytes, an explicit source allowlist and
per-file hashes. It does not replace Blender's manifest rules. Validate a
finished package with an explicitly selected Blender executable:

```sh
/path/to/blender --background --factory-startup --offline-mode \
  --command extension validate /external/sith-package.zip
```

Optional manifest fields should be omitted when unused; explicit empty values
or lists are invalid under the
[official extension manifest instructions](https://docs.blender.org/manual/en/5.2/advanced/extensions/getting_started.html).

Background execution, factory startup, automatic-script controls, thread limits
and offline preference controls are existing Blender facilities. The companion
adds job isolation and receipts around them. `--offline-mode` expresses Blender's
online-access preference; it is not an operating-system network sandbox. See
the [Blender command-line reference](https://docs.blender.org/manual/en/5.1/advanced/command_line/arguments.html).

## Runtime and feature scope

The current stable line at this review is Blender **5.2 LTS**, with **5.2.3**
released on October 22, 2026. Blender 5.3 is beta and 5.4 is alpha. These statuses
come from the [official releases page](https://www.blender.org/releases/) and
[5.2 release history](https://www.blender.org/releases/5-2/). A passing 5.1.2 test
does not establish 5.2, beta or alpha compatibility; use the release build notes
for the exact versions actually exercised.

### macOS architecture scope

Blender **4.5 LTS is the last official Intel Mac release**. Blender 5.0 and
later have official macOS builds for Apple Silicon, not Intel. This add-on's
minimum is Blender 5.0, so its ordinary macOS release target is Apple Silicon
(`macos-arm64`). See the [official system requirements](https://www.blender.org/download/requirements/)
and [Intel Mac deprecation announcement](https://devtalk.blender.org/t/deprecation-and-removal-of-macos-intel-builds-in-blender-5-0/38835).

The `macos-x64` manifest identifier remains meaningful for packages targeting
older Blender releases or independently compiled Intel builds. Its presence in
a manifest would not provide an official Blender 5+ Intel runtime, establish
Blender 4.5 compatibility, or prove that this add-on works on a custom Intel
build. Blender's announcement permits community-built Intel versions, but that
route needs its own build and native tests. Native macOS validation remains
separate from package-format validation and Linux execution of Python source.

Blender 5.2 also adds general inspection facilities such as an all-data-block
iterator, expanded external-file enumeration, and direct window screenshots.
They belong in Blender's general automation surface, and are not reasons to
reimplement the same features in the Sith-format server. See the
[5.2 release notes](https://www.blender.org/download/releases/5-2/).

The existing Sith limitations remain explicit: conversion imports geoset zero,
does not load referenced textures or palettes, and cannot normalize JK 2.1 UVs
using absent texture dimensions. Broader Blender features do not by themselves
repair format-specific exporter or importer limitations. See [MCP scope](MCP.md)
and [fork limitations](../FORK.md).
