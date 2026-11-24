# Indexed MAT dithering and the 8-bit and 16-bit editions

`sith.material.matWriter.encodeMat(..., dither=True)` writes Indexed MATs with an
ordered 4x4 (Bayer) pattern of two palette entries where the palette has no close
color. A pattern never adds a palette entry that the CMP never darkens
(fullbright), and `avoidFullbright=True` keeps those entries out of Indexed output
altogether. The 3DO exporter can write every MAT twice in one run: an Indexed
8-bit edition and an RGB565 16-bit edition with the same file names.

## Why

The fork's Indexed writer chooses the nearest palette entry for each texel. The
stock JK palette has no skin ramp, so nearest color turns a skin gradient into
bands of orange and grey. Coda's rule for Andrei's characters is nearest color,
skin dithered. Earlier release tools used masks to choose the dithered regions,
and a 4x4 two-color pattern for a color the palette lacked. Error diffusion was
tried and drew visible worms; it also depends on scan order.

Andrei ships 16-bit RGB565 MATs for 3D acceleration and an 8-bit edition with the
same names in a separate GOB for the software renderer.

## What dithering does

For each distinct texel color `c` the writer looks at the 12 palette entries
nearest to it. It chooses a majority entry `a`, a minority entry `b` and a share
`k` from 8 to 16 that minimize:

```text
|c - m|^2 + 1/4 * r * (1 - r) * |a - b|^2,   m = (k*a + (16-k)*b) / 16,   r = k / 16
```

The first term is the error of the 4x4 tile's average color. The second is a
quarter of the pattern's variance. It keeps a color solid when one entry is close.
For example, with palette greys 100 and 140 only, values 106 to 134 dither and the
rest use one grey.

The texel in stored row `y` and column `x` gets `a` where
`kBayer4[y % 4][x % 4] < k`, otherwise `b`:

```text
 0  8  2 10
12  4 14  6
 3 11  1  9
15  7 13  5
```

Each 4x4 tile then holds exactly `k` majority texels, and its average is `m`.
The costs are exact integers on 8-bit values. Ties prefer a solid color, then the
larger majority, then lower palette indices. The output depends only on the
pixels, the mask and the palette.

- **Mask.** `MatCel(width, height, pixels, ditherMask)` takes one value per texel,
  bottom row first like the pixels. Texels with a value of at least 0.5 may
  dither. The others use the nearest entry, exactly as without dithering.
- **Mip levels.** Each level is dithered from its own filtered pixels, with the
  pattern anchored at its first stored texel. The mask is area-reduced with the
  pixels. A mip texel may dither when at least half of its area is masked. A
  1x1 level takes the majority entry.
- **Transparency.** If a cel has alpha below 0.5, index 0 stays reserved for
  transparent texels and no pattern uses it.
- **Fullbright entries.** A pattern never adds an entry the CMP never darkens;
  see [Fullbright entries](#fullbright-entries).
- **Default.** Without `dither=True` the output is unchanged. A core test pins the
  SHA-256 of release 1.2.0's Indexed output.

On a synthetic skin gradient and a synthetic 256-color palette without skin
tones, the mean error of 4x4 block averages falls from 20.7 to 8.9 (8-bit RGB
units). `tests/core/test_mat_dither.py` checks a smaller form of this.

## Fullbright entries

JKDF2 and MOTS draw each texel of an 8-bit texture through the CMP's light
table: 64 rows of 256 palette indices, one row per light level, row 63 for full
light. `rdColormap_LoadEntry` (`Engine/rdColormap.c` in OpenJKDF2) reads the table
after the palette, and the software rasterizers draw each texel's index through
the row of its light level (`Raster/rdZRaster.c`: "Colormap light table
(per-vertex intensity indexes it)"). OpenJKDF2's GL renderer uploads the same
table for 8-bit textures (`Platform/GL/std3D.c`, `worldpal_lights_texture`). An
entry that every row draws as the same index looks the same in shadow as in full
light. It is fullbright, and a texel that uses it glows in shadow.

In the stock `dflt.cmp` the fullbright entries are 0 to 31 and 63, each drawn as
itself at every level: the pure greens, reds, yellows and oranges, whites, pale
blues and a blue used for lights and effects, and black.

`ColorMap.load` keeps the light table of a JKDF2/MOTS CMP (version 0x1E) as
`lightLevels`, and `fullbrightIndices(palette)` lists the entries whose 64 levels
all hold one index. The writer follows three rules:

1. Dithering never adds a fullbright entry that nearest color would not use for
   that texel. Where a texel's nearest entry is fullbright, the texel stays solid
   with it; elsewhere fullbright entries are not candidates.
2. `avoidFullbright=True` (**Avoid Fullbright Colours** in the 3DO and MAT
   export dialogs) keeps fullbright entries out of nearest color and dithering
   alike, for skin and cloth that must take shadow. Transparent texels still use
   index 0, the transparency key. With the editions it applies to the 8-bit
   edition only.
3. With the option and dithering off, the output is byte for byte what it is
   without a light table, as in release 1.2.0.

A palette without a light table, such as an array or a CMP file that ends after
its palette, has no known fullbright entries: rule 1 changes nothing and
`avoidFullbright` is refused. A Grim Fandango CMP (version 0x14) is read the same
way, because its layout after the palette is not sourced here.

Nearest color still picks a fullbright entry for some colors, such as a
saturated red. On the stock CMP, 20,000 random colors in one cel use a
fullbright entry in 2,552 texels by nearest color. Dithering without rule 1
added one to 1,417 other texels; with rule 1 it adds none, and
`avoidFullbright` leaves none. `tests/core/test_mat_dither.py` checks each rule
on synthetic palettes.

## What the editions do

`export3do(..., exportTextures=True, textureEditions=True, texturePalette=cmp)`
writes each MAT twice:

| Edition | Encoding | Folder option | Default |
| --- | --- | --- | --- |
| 8-bit | Indexed with the CMP | `textureDirectory8bit` | `mat8` beside the 3DO |
| 16-bit | RGB565 | `textureDirectory16bit` | `mat16` beside the 3DO |

The two editions are not the same texture in two encodings when a cel has
transparency. The 8-bit edition stores texels with alpha below 0.5 as index 0
and marks the texture transparent. The 16-bit edition is RGB565: it has no
alpha, and its texture headers leave transparency off (`alpha_en` 0;
`rdMaterial_LoadEntry` keys out a color only when it is set). Texels that are
see-through in the 8-bit edition are opaque in the 16-bit edition, so a cut-out
texture such as hair cards renders solid in the 16-bit GOB. The export warns
for each such image: "The 16-bit edition discards alpha from ...". Faces with
the **Translucent** flag still blend in both editions: the hardware renderer
draws them at alpha 90 whatever the MAT (`rdCache_SendFaceListToHardware`).

Both folders follow the **MAT Directory** rules: relative paths resolve beside
the 3DO; absolute and blend-relative `//` paths are used as given. They must be
different folders. Two spellings of one folder are refused before any file is
written: the same path in another case (one folder on case-insensitive volumes,
the macOS and Windows defaults), or two paths to one existing folder, such as a
link or a bind mount. Every MAT of both editions is encoded before any file changes.
The model and both editions are then published together, and a failure restores
the previous files. Existing MATs are kept unless overwriting is enabled.
`textureDither` and `textureAvoidFullbright` apply to the 8-bit edition only.
Grim Fandango exports reject editions.

## Masks in Blender

Add an Image custom property named `sith_dither_mask_0` to the material
(`sith_dither_mask_1` for cel 1, and so on). The image must have the cel's size.
Texels whose red channel is at least 0.5 may dither. A material without the
property dithers every texel that needs it. The mask is read only when dithering
is enabled.

## Run it from Python

```python
import numpy as np
from sith.material.cmp import ColorMap
from sith.material.matWriter import MatCel, encodeMat, fullbrightIndices

palette = ColorMap.load('dflt.cmp')                     # keeps its light table
print(fullbrightIndices(palette))                       # [0, 1, ..., 31, 63] for the stock CMP
pixels = np.zeros((64, 64, 4)); pixels[..., 3] = 1     # RGBA in [0, 1], bottom row first
skin = np.zeros((64, 64)); skin[:, :32] = 1             # dither only the left half
data = encodeMat([MatCel(64, 64, pixels, skin)], 'INDEXED', palette, dither=True)
shaded = encodeMat([MatCel(64, 64, pixels)], 'INDEXED', palette, avoidFullbright=True)
```

In Blender:

```python
from sith.model.model3doExporter import export3do
from sith.model.model3doLoader import Model3doFileVersion

export3do(obj, 'character.3do', Model3doFileVersion.Version2_1, True, False, True,
          exportTextures=True, textureEditions=True, texturePalette='dflt.cmp',
          textureDither=True, textureAvoidFullbright=False, warnings=[])
```

`exportMat(source, path, 'INDEXED', palette, dither=True, avoidFullbright=False)`
does the same for one image or material.

## Read the result

The result is an ordinary MAT. Decode it with the CMP to compare it with an
undithered export: texels outside the mask, and colors the palette matches, have
the same index in both.

## Limits

- The average is computed on stored 8-bit values, not in linear light. For the
  close pairs the writer picks the difference is small. A pattern of two far-apart
  entries looks lighter on screen than its computed average.
- The renderer shades each index through the CMP's light table. The pattern's
  average under lighting is not modelled: the table can darken a pattern's two
  entries differently.
- The fullbright entries come from the CMP given at export. The game draws a
  level's 8-bit textures with that level's CMP; one with other fullbright entries
  is not checked. Only the stock `dflt.cmp` was examined.
- An entry counts as fullbright only when every level draws the same index. An
  entry the table darkens only a little is not reported.
- **Avoid Fullbright Colours** applies to every texture of an export. Export a
  texture meant to glow, such as eyes, separately without it.
- The glow follows from the light table and the renderer source; it was not
  watched in the game.
- The 4x4 pattern is visible up close.
- Only the 12 nearest entries are considered, so a better pair of far entries can
  be missed.
- There is no error diffusion, palette generation or color-profile handling.
- Dithering is slower than nearest color: about one second per 40,000 distinct
  colors, and about 3 seconds for a 256x256 cel of random colors with its mips.
