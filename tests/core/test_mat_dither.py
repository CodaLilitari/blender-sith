# SPDX-License-Identifier: GPL-3.0-or-later
"""Ordered (Bayer 4x4) dithering of indexed MATs on synthetic images and palettes."""
import hashlib
import struct
import unittest

import numpy as np

from sith.material.matWriter import (MatCel, _dither_choices, encodeMat, kBayer4, kDitherCandidates,
                                     kDitherContrast)


def ramps():
    """A synthetic game-like palette: 16-step ramps of a few hues, none of them skin."""
    steps = np.linspace(0.08, 1.0, 16)[:, None]
    hues = ((255, 255, 255), (255, 140, 40), (230, 40, 30), (150, 90, 50), (90, 110, 200),
            (250, 220, 120), (200, 120, 140), (60, 150, 60), (120, 60, 160), (40, 180, 180),
            (255, 140, 40), (180, 180, 150), (100, 70, 60), (210, 80, 80), (140, 160, 190), (255, 255, 120))
    return np.floor(np.concatenate([steps * np.array(hue) for hue in hues]) + 0.5).astype(np.int64)


def two_greys():
    """Grey 100 at index 3 and grey 140 at index 4; every other entry is far from grey."""
    palette = np.tile(np.array([(255, 0, 255)]), (256, 1))
    palette[3], palette[4] = (100, 100, 100), (140, 140, 140)
    palette[200] = (0, 255, 0)
    return palette


def skin(height, width):
    """A synthetic skin-tone gradient, RGBA in [0, 1], bottom row first."""
    y, x = np.mgrid[0:height, 0:width] / np.array([max(height - 1, 1), max(width - 1, 1)])[:, None, None]
    light = 0.35 + 0.65 * (1 - y)
    rgb = np.floor(np.stack((236 - 20 * x, 188 - 30 * x, 160 - 18 * x), -1) * light[..., None] + 0.5)
    return np.concatenate((rgb / 255, np.ones((height, width, 1))), axis=2)


def flat(color, height, width, alpha=1.0):
    return np.tile(np.array([*np.asarray(color) / 255, alpha]), (height, width, 1))


def levels(data, cel=0):
    """Stored indices of each mip level of one 8-bit cel, top row first like the file."""
    offset = 76 + 40 * struct.unpack_from('<i', data, 12)[0]  # Header, then one record per cel.
    for _ in range(cel + 1):
        width, height, _, _, _, count = struct.unpack_from('<6i', data, offset)
        offset += 24
        result = []
        for level in range(count):
            w, h = max(1, width >> level), max(1, height >> level)
            result.append(np.frombuffer(data[offset:offset + w * h], dtype=np.uint8).reshape(h, w))
            offset += w * h
    return result


def base(cels, palette, **options):
    return levels(encodeMat(cels, 'INDEXED', palette, generateMipmaps=False, **options))[0]


def brute_force(colors, palette, first):
    """Enumerate every (majority, minority, share of 16) among the candidates; documented tie order."""
    weight, scale = kDitherContrast
    palette = np.asarray(palette, dtype=np.int64)
    result = []
    for color in np.asarray(colors, dtype=np.int64):
        distance = ((color - palette[first:]) ** 2).sum(axis=1)
        candidates = np.sort(np.argsort(distance, kind='stable')[:kDitherCandidates] + first)
        a, b, share = (grid.ravel() for grid in
                       np.meshgrid(candidates, candidates, np.arange(8, 17), indexing='ij'))
        span = ((palette[a] - palette[b]) ** 2).sum(axis=1)
        valid = np.where(share == 16, a == b, (a != b) & (span > 0))
        error = 16 * color - share[:, None] * palette[a] - (16 - share)[:, None] * palette[b]
        cost = scale * (error * error).sum(axis=1) + weight * share * (16 - share) * span
        order = np.lexsort((b, a, 16 - share, share < 16, cost))  # The last key sorts first.
        best = order[valid[order]][0]
        result.append((a[best], b[best], share[best]))
    return np.array(result)


def block_error(indices, palette, source, size=4):
    """Mean RGB distance between size x size block averages of the output and of the source."""
    out = palette[indices].astype(float)
    src = np.flip(np.floor(source[..., :3] * 255 + 0.5), axis=0)
    h, w = (out.shape[0] // size) * size, (out.shape[1] // size) * size
    def blocks(image):
        return image[:h, :w].reshape(h // size, size, w // size, size, 3).mean(axis=(1, 3))
    return float(np.sqrt(((blocks(out) - blocks(src)) ** 2).sum(axis=-1)).mean())


class DitherTests(unittest.TestCase):
    def test_default_indexed_output_is_byte_identical_to_release_1_2_0(self):
        # SHA-256 of these encodings by sith/material/matWriter.py at release 1.2.0 (34d256c).
        i = np.arange(256)
        palette = np.stack(((i * 37) % 256, (i * 91 + 17) % 256, (i * 53 + 101) % 256), -1)
        y, x = np.mgrid[0:16, 0:24]
        rgba = np.stack(((x * 11 + y * 3) % 256, (x * 5 + y * 13 + 40) % 256,
                         (x * 7 + y * 17 + 90) % 256, np.full_like(x, 255)), -1) / 255.0
        holes = rgba.copy()
        holes[2:4, 3:7, 3] = 0.0
        cels = [MatCel(24, 16, rgba), MatCel(24, 16, holes), MatCel(5, 3, rgba[:3, :5])]
        expected = (({}, '87170ce86bdcc8ace498e6d892ea472360be7f1d49d2542b422946b6846bb4c8'),
                    ({'generateMipmaps': False}, '19b91cccd9ed75eefabecc5c89ba2343863acbef8fd7c7babd2473ebb609ff36'),
                    ({'grim': True}, '5f5cb9773086440269b8be3daeae5dcab79bc4f9ae90c7bbe513503a68fa39d2'))
        for options, digest in expected:
            for dither in ({}, {'dither': False}):
                with self.subTest(options=options, dither=dither):
                    data = encodeMat(cels, 'INDEXED', palette, **options, **dither)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_bayer_matrix_spreads_every_share_over_the_tile(self):
        self.assertEqual(sorted(kBayer4.ravel()), list(range(16)))
        np.testing.assert_array_equal(kBayer4, ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)))
        np.testing.assert_array_equal(kBayer4 < 8, np.indices((4, 4)).sum(axis=0) % 2 == 0)  # Checkerboard.
        for share in (4, 8, 12):
            per_block = (kBayer4 < share).reshape(2, 2, 2, 2).sum(axis=(1, 3))
            np.testing.assert_array_equal(per_block, np.full((2, 2), share // 4))

    def test_fast_pair_search_matches_a_full_enumeration(self):
        steps = np.arange(160)
        colors = np.stack(((steps * 97 + 13) % 256, (steps * 59 + 101) % 256, (steps * 23 + 7) % 256), -1)
        colors = np.concatenate((colors, [(0, 0, 0), (255, 255, 255), (120, 120, 120), (230, 175, 150)]))
        duplicated = np.repeat(ramps()[::16], 16, axis=0)  # Equal entries at different indices.
        for name, palette in (('ramps', ramps()), ('greys', two_greys()), ('duplicates', duplicated)):
            for first in (0, 1):
                with self.subTest(palette=name, first=first):
                    np.testing.assert_array_equal(_dither_choices(colors, palette, first),
                                                  brute_force(colors, palette, first))

    def test_a_colour_halfway_between_two_entries_alternates_them_evenly(self):
        stored = base([MatCel(8, 8, flat((120, 120, 120), 8, 8))], two_greys(), dither=True)
        np.testing.assert_array_equal(stored, np.where(np.tile(kBayer4, (2, 2)) < 8, 3, 4))
        self.assertEqual(two_greys()[stored].mean(), 120)

    def test_colours_on_or_near_a_palette_entry_stay_solid(self):
        for value, index in ((100, 3), (104, 3), (140, 4), (136, 4)):
            with self.subTest(value=value):
                stored = base([MatCel(4, 4, flat((value,) * 3, 4, 4))], two_greys(), dither=True)
                self.assertEqual(set(stored.ravel()), {index})

    def test_tile_averages_follow_a_skin_gradient_more_closely_than_nearest_colour(self):
        source, palette = skin(32, 32), ramps()
        nearest = base([MatCel(32, 32, source)], palette)
        dithered = base([MatCel(32, 32, source)], palette, dither=True)
        self.assertLess(block_error(dithered, palette, source), 0.6 * block_error(nearest, palette, source))

    def test_mask_limits_dithering_and_unmasked_texels_keep_the_nearest_colour(self):
        source, palette = skin(16, 16), ramps()
        mask = np.zeros((16, 16))
        mask[:, :8] = 1.0
        nearest = base([MatCel(16, 16, source)], palette)
        dithered = base([MatCel(16, 16, source, mask)], palette, dither=True)
        everywhere = base([MatCel(16, 16, source)], palette, dither=True)
        np.testing.assert_array_equal(dithered[:, 8:], nearest[:, 8:])
        np.testing.assert_array_equal(dithered[:, :8], everywhere[:, :8])
        self.assertTrue(np.any(dithered[:, :8] != nearest[:, :8]))
        for values in (mask.ravel() * 0.6, mask > 0, mask.tolist()):
            with self.subTest(kind=type(values).__name__):
                np.testing.assert_array_equal(base([MatCel(16, 16, source, values)], palette, dither=True), dithered)

    def test_mip_levels_dither_their_own_pixels_with_the_same_anchored_pattern(self):
        palette = two_greys()
        mask = np.zeros((16, 32))
        mask[:, :16] = 1
        stored = levels(encodeMat([MatCel(32, 16, flat((120, 120, 120), 16, 32), mask)], 'INDEXED', palette,
                                  dither=True))
        self.assertEqual([level.shape for level in stored], [(16, 32), (8, 16), (4, 8), (2, 4), (1, 2), (1, 1)])
        for level in stored:
            height, width = level.shape
            half = max(1, width // 2)
            with self.subTest(size=level.shape):
                np.testing.assert_array_equal(level[:, :half], np.where(np.tile(kBayer4, (4, 8))[:height, :half] < 8, 3, 4))
                if width > 1:
                    # Nearest colour of 120, equally far from 100 and 140: the lower index.
                    self.assertEqual(set(level[:, half:].ravel()), {3})

    def test_transparent_texels_keep_index_zero_and_patterns_never_use_it(self):
        palette = two_greys()
        palette[0] = (110, 110, 110)  # Closer to the source than either grey, but reserved when transparent.
        pixels = flat((120, 120, 120), 8, 8)
        pixels[:2, :2, 3] = 0.0
        stored = base([MatCel(8, 8, pixels)], palette, dither=True)
        self.assertEqual(set(stored[-2:, :2].ravel()), {0})  # Bottom rows are stored last.
        self.assertNotIn(0, set(stored[:-2].ravel()) | set(stored[-2:, 2:].ravel()))
        self.assertIn(0, set(base([MatCel(8, 8, flat((120, 120, 120), 8, 8))], palette, dither=True).ravel()))

    def test_results_are_deterministic_and_inputs_unchanged(self):
        source = skin(8, 12)
        mask = np.ones((8, 12))
        mask[0] = 0
        before = (source.copy(), mask.copy())
        cel = MatCel(12, 8, source, mask)
        first = encodeMat([cel, cel], 'INDEXED', ramps(), dither=True)
        self.assertEqual(first, encodeMat([cel, cel], 'INDEXED', ramps(), dither=True))
        np.testing.assert_array_equal(source, before[0])
        np.testing.assert_array_equal(mask, before[1])

    def test_grim_cels_dither_their_base_image(self):
        data = encodeMat([MatCel(4, 4, flat((120, 120, 120), 4, 4))], 'INDEXED', two_greys(), grim=True, dither=True)
        self.assertEqual(struct.unpack_from('<i', data, 8)[0], 3)  # Grim texture MAT type.
        stored = levels(data)
        self.assertEqual(len(stored), 1)
        np.testing.assert_array_equal(stored[0], np.where(kBayer4 < 8, 3, 4))

    def test_invalid_dither_requests_reject(self):
        pixels = flat((120, 120, 120), 2, 2)
        for encoding in ('RGBA32', 'RGB565'):
            with self.subTest(encoding=encoding), self.assertRaisesRegex(ValueError, 'Indexed'):
                encodeMat([MatCel(2, 2, pixels)], encoding, dither=True)
        with self.assertRaisesRegex(ValueError, 'requires dither'):
            encodeMat([MatCel(2, 2, pixels, np.ones(4))], 'INDEXED', two_greys())
        for mask in (np.ones(3), [1, np.nan, 1, 1], np.ones(4, dtype=complex)):
            with self.subTest(mask=mask), self.assertRaises(ValueError):
                encodeMat([MatCel(2, 2, pixels, mask)], 'INDEXED', two_greys(), dither=True)


if __name__ == '__main__':
    unittest.main()
