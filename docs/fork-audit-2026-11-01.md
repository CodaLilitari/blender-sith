# Fork and branch audit: 2026-11-01

The starting Coda commit `0f401e672f9e28818bc7304407bf1cb781e65b70`
already contains the current upstream master
`ecc90131bcfdeabd400225e8a72ce94cbff1ec5c`. No upstream commit was missing.
The corner-normal and hexadecimal-property fixes remain in the ancestry and
regression suite of both platform branches. Existing branches and releases
are retained; no history is rewritten.

## Source comparison

| Source | Observed head | Disposition |
| --- | --- | --- |
| [smlu master](https://github.com/smlu/blender-sith) | `ecc90131bcfdeabd400225e8a72ce94cbff1ec5c` | Already included, including Blender 5 modernization and transparency work |
| smlu `blender-2.79` | `24ca76cb82d71a17f79b46cb3e6d2b2bbea428d1` | Ancestor; retained as legacy history |
| smlu `key-export-fix` | `0438abf9ffab478cb07a11be83d3f81cdff30218` | Already merged by upstream PR 8 |
| smlu `key-import-fix` | `e1781ea8f67e8317390df1c16a89ae36710a22bc` | Already merged by upstream PR 7 |
| [sithmartyr master](https://github.com/sithmartyr/blender-sith) | `0a6626d8cb23ab4d7bc284ebf67b582e414d4c42` | Selective fix below; older Blender 4 port is superseded |
| [BlenderCN-Org master](https://github.com/BlenderCN-Org/blender-ijim) | `19399aa0ceb4cc5fe7652dac471df6f1d0cc797f` | No unique commits; ancestor of upstream |
| Coda GitHub, GitLab and GitHub pre-migration master | `0f401e672f9e28818bc7304407bf1cb781e65b70` | Matching starting revision; all original Coda changes preserved |

All listed branches of the public upstream fork network were inspected. The
three upstream pull requests returned by the service were already merged.
GitLab's visible Blender-Sith project search found the Coda add-on repository;
it had only master at the starting snapshot. Current GitHub metadata places
the active Coda repository in the upstream fork network; older documentation
described the pre-migration arrangement.

## Incorporated and verified changes

- Darth Martyr's [material-table separator fix](https://github.com/sithmartyr/blender-sith/commit/a25543f5ba45c5ad203a65ba11193c640b37e255)
  is cherry-picked with its original author and a source-commit trailer.
  Names of 15 or more characters now remain whitespace-separated from their
  numeric index. The regression failed before the fix and passed afterward.
- New Coda repair: missing CMP palettes no longer leave the indexed MAT reader
  positioned at the preceding cel's pixels. It consumes every mip level while
  retaining placeholder textures, checks truncated skipped data, and reads the
  next cel correctly. A synthetic two-cel/two-mip case reproduces the failure.
- Reproducible platform extension packaging and bounded background validation
  use external output directories and preserve GPL licensing.

The sithmartyr branch is not merged wholesale: its older MAT implementation
reads every mip at full dimensions, omits non-indexed decoding and forces
opaque materials. Its scene lookup also narrows to the current collection.
Those are regressions against the retained upstream implementation.
Its separate change to default export to 3DO 2.1 is a workflow preference;
the existing multi-game UI default remains. JK/MotS automation explicitly
selects `Version2_1` as before.

## Red and Lily provenance

Associated project records and three related local upstream checkouts were
examined. At the recovered checkpoint, those add-on copies are unmodified
upstream. No distinct Red-authored or Lily-authored add-on branch was found
in the inspected repositories or associated records. This is a bounded
finding, not a claim that no other historical work exists.

The recovered contributions are preserved in their existing project scope:
Red's model-conversion work and Lily's model geometry/baking work are native
asset workflows. Lily's advisory findings about image-node UV sizing,
material-name collisions, KEY/PUP contracts and format-specific color handling
remain relevant guidance. They are not relabeled as add-on code commits or
silently copied into this add-on. Their source artifacts were not modified.

## Validation boundary

Synthetic Blender 5.1.2 tests cover the existing ten export/metadata contracts,
the material-name separator, and indexed MAT cels with and without a palette.
Actual platform test results accompany the delivered artifacts. These checks
do not certify retail rendering, connected multiplayer, interactive extension
installation, other Blender versions, or all historical third-party assets.
The previously documented 3DO 2.2, modifier, transform and geoset limitations
remain; see [FORK.md](../FORK.md).
