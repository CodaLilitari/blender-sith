# Indexed MAT dithering and the 8-bit and 16-bit editions

`sith.material.matWriter.encodeMat(..., dither=True)` writes Indexed MATs with an
ordered 4x4 (Bayer) pattern of two palette entries where the palette has no close
color. The 3DO exporter can write every MAT twice in one run: an Indexed 8-bit
edition and an RGB565 16-bit edition with the same file names.

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
- **Default.** Without `dither=True` the output is unchanged. A core test pins the
  SHA-256 of release 1.2.0's Indexed output.

On a synthetic skin gradient and a synthetic 256-color palette without skin
tones, the mean error of 4x4 block averages falls from 20.7 to 8.9 (8-bit RGB
units). `tests/core/test_mat_dither.py` checks a smaller form of this.

## What the editions do

`export3do(..., exportTextures=True, textureEditions=True, texturePalette=cmp)`
writes each MAT twice:

| Edition | Encoding | Folder option | Default |
| --- | --- | --- | --- |
| 8-bit | Indexed with the CMP | `textureDirectory8bit` | `mat8` beside the 3DO |
| 16-bit | RGB565 | `textureDirectory16bit` | `mat16` beside the 3DO |

Both folders follow the **MAT Directory** rules: relative paths resolve beside
the 3DO; absolute and blend-relative `//` paths are used as given. They must be
different folders. Two spellings of one folder are refused before any file is
written: the same path in another case (one folder on case-insensitive volumes,
the macOS and Windows defaults), or two paths to one existing folder, such as a
link or a bind mount. Every MAT of both editions is encoded before any file changes.
The model and both editions are then published together, and a failure restores
the previous files. Existing MATs are kept unless overwriting is enabled.
`textureDither` dithers only the 8-bit edition. Grim Fandango exports reject
editions.

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
from sith.material.matWriter import MatCel, encodeMat

palette = ColorMap.load('dflt.cmp')
pixels = np.zeros((64, 64, 4)); pixels[..., 3] = 1     # RGBA in [0, 1], bottom row first
skin = np.zeros((64, 64)); skin[:, :32] = 1             # dither only the left half
data = encodeMat([MatCel(64, 64, pixels, skin)], 'INDEXED', palette, dither=True)
```

In Blender:

```python
from sith.model.model3doExporter import export3do
from sith.model.model3doLoader import Model3doFileVersion

export3do(obj, 'character.3do', Model3doFileVersion.Version2_1, True, False, True,
          exportTextures=True, textureEditions=True, texturePalette='dflt.cmp',
          textureDither=True, warnings=[])
```

`exportMat(source, path, 'INDEXED', palette, dither=True)` does the same for one
image or material.

## Read the result

The result is an ordinary MAT. Decode it with the CMP to compare it with an
undithered export: texels outside the mask, and colors the palette matches, have
the same index in both.

## Limits

- The average is computed on stored 8-bit values, not in linear light. For the
  close pairs the writer picks the difference is small. A pattern of two far-apart
  entries looks lighter on screen than its computed average.
- The software renderer shades each index through the CMP light tables. The
  pattern's average under lighting is not modelled.
- The 4x4 pattern is visible up close.
- Only the 12 nearest entries are considered, so a better pair of far entries can
  be missed.
- There is no error diffusion, palette generation or color-profile handling.
- Dithering is slower than nearest color: about one second per 40,000 distinct
  colors, and about 3 seconds for a 256x256 cel of random colors with its mips.
