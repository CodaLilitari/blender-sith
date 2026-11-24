# SPDX-FileCopyrightText: 2026 Crt Vavros
# SPDX-License-Identifier: GPL-3.0-or-later
"""MAT texture encoding, independent of Blender and its shader graph."""
import operator
from typing import NamedTuple

import numpy as np

from .mat import ColorFormat, ColorMode, MatType, cf_serf, file_magic, required_version, mh_serf, mtr_serf, mmm_serf

kMatEncodings = (
    ('RGBA32', 'RGBA32', '32-bit color with 8-bit alpha'),
    ('RGB24', 'RGB 24', '24-bit color without alpha'),
    ('RGB565', 'RGB 565', '16-bit color without alpha'),
    ('RGBA4444', 'RGBA 4444', '16-bit color with 4-bit alpha'),
    ('RGBA1555', 'RGBA 1555', '16-bit color with 1-bit alpha'),
    ('INDEXED', 'Indexed', '8-bit indices into the selected CMP palette; 1-bit transparency'),
)


class MatCel(NamedTuple):
    width: int
    height: int
    pixels: object  # Flat or HxWx4 normalized RGBA, bottom row first like Blender.
    ditherMask: object = None  # Optional flat or HxW values, bottom row first; >= 0.5 dithers.


# Bayer's 4x4 threshold order. Texel (row, column) of a stored level, counted from the first
# stored row and column, takes its majority colour where kBayer4[row % 4][column % 4] < share.
kBayer4 = np.array(((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)), dtype=np.int64)
kDitherCandidates = 12  # Nearest palette entries considered for each colour.
kDitherContrast = (1, 4)  # A pattern's variance counts 1/4 as much as the error of its average.


def _format(mode, bpp, bits, shifts):
    r, g, b, a = bits
    rs, gs, bs, als = shifts
    return ColorFormat(mode, bpp, r, g, b, rs, gs, bs,
                       8 - r if r else 0, 8 - g if g else 0, 8 - b if b else 0,
                       a, als, 8 - a if a else 0)


kMatColorFormats = {
    'RGBA32': _format(ColorMode.RGBA, 32, (8, 8, 8, 8), (16, 8, 0, 24)),
    'RGB24': _format(ColorMode.RGB, 24, (8, 8, 8, 0), (16, 8, 0, 0)),
    'RGB565': _format(ColorMode.RGB, 16, (5, 6, 5, 0), (11, 5, 0, 0)),
    'RGBA4444': _format(ColorMode.RGBA, 16, (4, 4, 4, 4), (8, 4, 0, 12)),
    'RGBA1555': _format(ColorMode.RGBA, 16, (5, 5, 5, 1), (10, 5, 0, 15)),
    'INDEXED': _format(ColorMode.Indexed, 8, (0, 0, 0, 0), (0, 0, 0, 0)),
}


def _dimension(value):
    try:
        value = operator.index(value)
    except TypeError as error:
        raise ValueError('MAT dimensions must be integers') from error
    if not 1 <= value <= 0x7fffffff:
        raise ValueError('MAT dimensions must be positive signed 32-bit values')
    return value


def _area_reduce(data, size, axis):
    """Integrate pixel areas, including odd-sized edges rather than dropping them."""
    values = np.moveaxis(data, axis, 0)
    length = len(values)
    positions = np.linspace(0, length, size + 1)
    indices = positions.astype(np.intp)
    prefix = np.concatenate((np.zeros_like(values[:1]), np.cumsum(values, axis=0)), axis=0)
    fractions = (positions - indices).reshape((-1,) + (1,) * (values.ndim - 1))
    integral = prefix[indices] + fractions * values[np.minimum(indices, length - 1)]
    reduced = np.diff(integral, axis=0) / (length / size)
    return np.moveaxis(reduced, 0, axis)


def _next_mip(pixels, alpha):
    data = pixels.copy()
    if alpha:
        data[..., :3] *= data[..., 3:4]
    data = _area_reduce(data, max(1, len(data) // 2), 0)
    data = _area_reduce(data, max(1, data.shape[1] // 2), 1)
    if alpha:
        # Transparent RGB must not bleed into the visible edge of smaller mips.
        np.divide(data[..., :3], data[..., 3:4], out=data[..., :3], where=data[..., 3:4] > 0)
    return np.clip(data, 0, 1)


def _palette(palette):
    if palette is None:
        raise ValueError('Indexed MAT encoding requires a CMP palette')
    values = np.asarray(getattr(palette, 'palette', palette))
    if (values.shape != (256, 3) or values.dtype.kind not in 'iuf'
            or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 255)
            or np.any(values != np.floor(values))):
        raise ValueError('CMP palette must contain 256 RGB byte triples')
    return values.astype(np.float32)


def _fullbright(palette):
    """Boolean mask of the palette entries the CMP never darkens, or None without a light table.

    The light table (``ColorMap.lightLevels``) maps each index to the index drawn at each of
    64 light levels; the JKDF2/MOTS software rasterizers draw every texel through it
    (Raster/rdZRaster.c), and OpenJKDF2's GL renderer uploads the same table for 8-bit
    textures (Platform/GL/std3D.c, ``worldpal_lights_texture``). An entry whose 64 levels all
    hold one index looks the same in shadow as in full light: it is fullbright. The stock
    ``dflt.cmp`` has 33, indices 0 to 31 and 63, each mapped to itself.
    """
    levels = getattr(palette, 'lightLevels', None)
    if levels is None:
        return None
    levels = np.asarray(levels)
    if (levels.shape != (64, 256) or levels.dtype.kind not in 'iu'
            or np.any(levels < 0) or np.any(levels > 255)):
        raise ValueError('A CMP light table must hold 64 rows of 256 palette indices')
    return np.all(levels == levels[:1], axis=0)


def fullbrightIndices(palette):
    """Sorted palette indices the CMP's light table never darkens, or None without a light table.

    ``palette`` is a ``ColorMap`` or any object with ``lightLevels``; see ``_fullbright``.
    """
    mask = _fullbright(palette)
    return None if mask is None else [int(index) for index in np.flatnonzero(mask)]


def _dither_choices(colors, palette, first, allowed=None):
    """Majority index, minority index and majority share (8 to 16 of 16) for each byte colour.

    A 4x4 tile that shows the majority entry ``a`` in ``k`` texels and the minority entry ``b``
    in the rest averages ``(k*a + (16-k)*b) / 16``. For each colour ``c`` the choice minimizes
    ``|c - average|^2 + 1/4 * (k/16) * (1 - k/16) * |a - b|^2``: the error of the tile's average
    plus a quarter of the pattern's variance, so a colour close to one palette entry stays
    solid. Candidates are the colour's ``kDitherCandidates`` nearest entries from ``first``
    among those ``allowed`` (a boolean mask over the palette; every entry when None), lower
    index first on equal distance. Costs are exact integers in stored 8-bit values,
    not in linear light. Ties prefer a solid colour, then the larger majority, then lower
    indices. The cost is a convex quadratic in ``k`` for each pair, so only the two integer
    shares around its vertex are evaluated; tests compare this with a full enumeration.
    """
    colors = np.asarray(colors, dtype=np.int64)
    palette = np.asarray(palette, dtype=np.int64)
    pool = np.arange(first, len(palette))
    if allowed is not None:
        pool = pool[np.asarray(allowed, dtype=bool)[first:]]
    if len(colors) and not len(pool):
        raise ValueError('No palette entry is left to dither with')
    weight, scale = kDitherContrast
    count = min(kDitherCandidates, len(pool))
    pSlot, qSlot = np.triu_indices(count, 1)  # Candidate slots are sorted by palette index.
    excluded = 1 << 30  # Above any real cost (< 2**28), small enough for the int64 key below.
    result = np.empty((len(colors), 3), dtype=np.int64)
    for offset in range(0, len(colors), 1024):
        color = colors[offset:offset + 1024]
        rows = np.arange(len(color))
        distance = np.sum((color[:, None, :] - palette[None, pool, :]) ** 2, axis=2)
        nearest = np.sort(pool[np.argsort(distance, axis=1, kind='stable')[:, :count]], axis=1)
        entries = palette[nearest]
        solid = 16 * (color[:, None, :] - entries)
        options = [(scale * np.sum(solid * solid, axis=2), nearest, nearest, np.full(nearest.shape, 16))]

        # Pair (p, q), p the lower palette index: share k of p averages q + k * (p - q) / 16.
        p, q = nearest[:, pSlot], nearest[:, qSlot]
        step = entries[:, pSlot] - entries[:, qSlot]
        base = 16 * (color[:, None, :] - entries[:, qSlot])
        span = np.sum(step * step, axis=2)
        with np.errstate(divide='ignore', invalid='ignore'):
            vertex = ((2.0 * scale * np.sum(base * step, axis=2) - 16.0 * weight * span)
                      / (2.0 * (scale - weight) * span))
        below = np.clip(np.floor(np.where(span > 0, vertex, 0.0)), 0, 16).astype(np.int64)
        for share in (below, np.minimum(below + 1, 16)):
            residual = base - share[..., None] * step
            cost = scale * np.sum(residual * residual, axis=2) + weight * share * (16 - share) * span
            mixed = (share > 0) & (share < 16) & (span > 0)  # Pure ends are solid options above.
            pMajor = share >= 8
            options.append((np.where(mixed, cost, excluded), np.where(pMajor, p, q),
                            np.where(pMajor, q, p), np.where(pMajor, share, 16 - share)))
        cost, majority, minority, share = (np.concatenate(values, axis=1) for values in zip(*options))
        key = ((cost * 2 + (share < 16)) * 17 + (16 - share)) * 65536 + majority * 256 + minority
        pick = np.argmin(key, axis=1)
        result[offset:offset + len(color)] = np.stack(
            (majority[rows, pick], minority[rows, pick], share[rows, pick]), axis=1)
    return result


def _dithered(indices, rgb, mask, palette, first, fullbright=None):
    """Replace masked texels with their colour's ordered two-entry pattern (bottom row first).

    With a ``fullbright`` mask, dithering never adds a fullbright entry that ``indices`` (the
    nearest colour) does not already hold: a texel whose nearest entry is fullbright stays
    solid, and the others choose among the entries that are not fullbright.
    """
    height, width = indices.shape
    selected = np.asarray(mask, dtype=bool).reshape(-1)
    allowed = None
    if fullbright is not None:
        selected = selected & ~fullbright[indices.reshape(-1)]
        allowed = ~fullbright
    if not selected.any():
        return indices
    colors, inverse = np.unique(rgb[selected], axis=0, return_inverse=True)
    majority, minority, share = _dither_choices(colors, palette, first, allowed)[inverse.reshape(-1)].T
    # Pixels arrive bottom row first; the threshold follows the stored (top-first) rows.
    rows = (height - 1 - np.arange(height))[:, None] % 4
    threshold = kBayer4[rows, np.arange(width)[None, :] % 4].reshape(-1)[selected]
    flat = indices.reshape(-1).copy()
    flat[selected] = np.where(threshold < share, majority, minority)
    return flat.reshape(height, width)


def _indexed(pixels, palette, transparent, dither=None, fullbright=None, avoidFullbright=False):
    rgb = np.floor(pixels[..., :3] * 255 + 0.5).astype(np.uint8).reshape((-1, 3))
    colors, inverse = np.unique(rgb, axis=0, return_inverse=True)
    first = 1 if transparent else 0
    usable = np.arange(first, len(palette))
    if avoidFullbright:
        usable = usable[~fullbright[first:]]
        if not len(usable):
            raise ValueError('Every palette entry this texture may use is fullbright; Avoid Fullbright Colours leaves none')
    nearest = np.empty(len(colors), dtype=np.uint8)
    # Bound scratch memory independently of the image dimensions. Repeated
    # colors share one search; equal distances choose the lowest palette index.
    for offset in range(0, len(colors), 2048):
        delta = colors[offset:offset + 2048].astype(np.float32)[:, None, :] - palette[None, usable, :]
        nearest[offset:offset + 2048] = usable[np.argmin(np.sum(delta * delta, axis=2), axis=1)]
    indices = nearest[inverse].reshape(pixels.shape[:2])
    if dither is not None and np.any(dither):
        indices = _dithered(indices, rgb, dither, palette, first, fullbright)
    if transparent:
        indices[pixels[..., 3] < 0.5] = 0
    return np.flip(indices, axis=0).tobytes()


def _dither_mask(cel, width, height):
    """Base-level dither coverage of one cel: 1 where dithering applies, 0 elsewhere."""
    values = getattr(cel, 'ditherMask', None)
    if values is None:
        return np.ones((height, width))
    if np.iscomplexobj(values):
        raise ValueError('MAT dither mask values must be real numbers')
    mask = np.asarray(values, dtype=np.float64)
    if mask.size != width * height or not np.isfinite(mask).all():
        raise ValueError('MAT dither mask must contain one finite value per texel')
    return (mask.reshape((height, width)) >= 0.5).astype(np.float64)


def _next_coverage(coverage):
    """Reduce a dither coverage map exactly as _next_mip reduces the pixels."""
    coverage = _area_reduce(coverage, max(1, len(coverage) // 2), 0)
    return _area_reduce(coverage, max(1, coverage.shape[1] // 2), 1)


def _packed(pixels, info):
    words = np.zeros(pixels.shape[:2], dtype=np.uint32)
    for channel, bits, shift in zip(range(4),
                                    (info.red_bpp, info.green_bpp, info.blue_bpp, info.alpha_bpp),
                                    (info.red_shl, info.green_shl, info.blue_shl, info.alpha_shl)):
        if bits:
            quantized = np.floor(pixels[..., channel] * ((1 << bits) - 1) + 0.5).astype(np.uint32)
            words |= quantized << shift
    words = np.flip(words, axis=0)
    if info.bpp == 24:
        return words.astype('<u4').view(np.uint8).reshape((-1, 4))[:, :3].tobytes()
    return words.astype('<u2' if info.bpp == 16 else '<u4').tobytes()


def encodeMat(cels, encoding='RGBA32', palette=None, *, generateMipmaps=True, grim=False, dither=False,
              avoidFullbright=False):
    """Encode editable cel pixels, not Blender shaders. HDR values clamp to [0,1].

    Every input cel uses normalized numeric channels in the decoder's domain.
    Packed fields round to nearest. Indexed output quantizes RGB to bytes, then
    chooses the nearest CMP entry in RGB space. Alpha below 0.5 uses reserved
    palette index zero; opaque pixels cannot reuse that index.

    ``dither=True`` (Indexed only) replaces the nearest entry with an ordered 4x4
    (Bayer) pattern of two entries where the pattern's average is enough closer to
    the source colour to outweigh its contrast; see ``_dither_choices``. A cel's ``ditherMask``
    limits it to texels whose mask value is at least 0.5. Each mip level is
    dithered from its own filtered pixels, with the threshold anchored at its first
    stored texel; the mask is area-reduced with the pixels and a mip texel is
    dithered when at least half of its area is masked. When the palette has a light
    table, dithering never adds a fullbright entry (``_fullbright``) that nearest colour
    would not use: a texel whose nearest entry is fullbright stays solid, and the others
    dither among the other entries. The result depends only on the pixels, the mask and
    the palette. Without ``dither`` the output is unchanged.

    ``avoidFullbright=True`` (Indexed only) keeps fullbright entries out of nearest colour
    and dithering alike, for surfaces that must take shadow; it needs a palette with a light
    table. Transparent texels still use index 0. Without it the nearest colours are unchanged.
    """
    if encoding not in kMatColorFormats:
        raise ValueError(f'Unknown MAT encoding: {encoding}')
    if grim and encoding != 'INDEXED':
        raise ValueError('Grim Fandango MAT textures require Indexed encoding')
    if dither and encoding != 'INDEXED':
        raise ValueError('Dithering applies only to Indexed MAT encoding')
    if avoidFullbright and encoding != 'INDEXED':
        raise ValueError('Avoiding fullbright colours applies only to Indexed MAT encoding')
    cels = list(cels)
    if not 1 <= len(cels) <= 0x7fffffff:
        raise ValueError('MAT requires at least one texture cel')
    info = kMatColorFormats[encoding]
    colors = _palette(palette) if encoding == 'INDEXED' else None
    fullbright = _fullbright(palette) if encoding == 'INDEXED' else None
    if avoidFullbright and fullbright is None:
        raise ValueError('Avoiding fullbright colours needs the light table of a JKDF2/MOTS CMP; this palette has none')
    output = bytearray(mh_serf.pack(file_magic, required_version,
                                  MatType.GrimTexture if grim else MatType.Texture, len(cels), len(cels)))
    output.extend(cf_serf.pack(*info))
    for index in range(len(cels)):
        # Match rdMaterial_Write's texture record, including its unit scalar.
        output.extend(mtr_serf.pack(8, 0, 0, 0, 0, 0x3f800000, 0, 0, 0, index))
    for cel in cels:
        width, height = _dimension(cel.width), _dimension(cel.height)
        if np.iscomplexobj(cel.pixels):
            raise ValueError('MAT pixels must be real RGBA values')
        pixels = np.asarray(cel.pixels, dtype=np.float64)
        if pixels.size != width * height * 4 or not np.isfinite(pixels).all():
            raise ValueError('MAT cel must contain finite RGBA pixels matching its dimensions')
        pixels = np.clip(pixels.reshape((height, width, 4)), 0, 1)
        transparent = encoding == 'INDEXED' and bool(np.any(pixels[..., 3] < 0.5))
        if getattr(cel, 'ditherMask', None) is not None and not dither:
            raise ValueError('A MAT dither mask requires dither=True')
        levels = [pixels]
        coverage = [_dither_mask(cel, width, height) if dither else None]
        # Grim readers advance to the next cel directly after its base pixels.
        if generateMipmaps and not grim:
            while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
                levels.append(_next_mip(levels[-1], encoding == 'INDEXED' or info.alpha_bpp > 0))
                coverage.append(None if coverage[-1] is None else _next_coverage(coverage[-1]))
        output.extend(mmm_serf.pack(width, height, int(transparent), 0, 0, len(levels)))
        for level, masked in zip(levels, coverage):
            if colors is None:
                output.extend(_packed(level, info))
            else:
                output.extend(_indexed(level, colors, transparent, None if masked is None else masked >= 0.5,
                                       fullbright, avoidFullbright))
    return bytes(output)
