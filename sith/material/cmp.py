# Sith Blender Addon
# Copyright (C) 2019-2026 Crt Vavros
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import numpy as np
from pathlib import Path
from struct import Struct
from typing import NamedTuple, List, Optional, Union

file_magic         = b'CMP '
supported_versions = [
    0x14, # Grim Fandango
    0x1E  # Star Wars JKDF2, MOTS, DroidWorks
]

kCmpLightLevels = 64  # Rows of the light table that follows a JKDF2/MOTS palette.

class CmpHeader(NamedTuple):
    format = Struct('<4sii52s')
    signature: bytes
    version: int
    hasAlphaTable: bool
    unknown: bytes

class CmpPaletteRGB(NamedTuple):
    r: int
    g: int
    b: int

class ColorMap:
    palette: List[CmpPaletteRGB]
    # (64, 256) uint8 light table, or None: row ``level`` holds the palette index drawn for each
    # index at that light level, row 63 at full light.
    lightLevels: Optional[np.ndarray] = None

    @classmethod
    def load(cls, filePath: Union[Path, str]) -> 'ColorMap':
        """
        Loads palette from cmp file, and the light table of a JKDF2/MOTS file.
        :`filePath`: Path to the cmp file.

        In JKDF2 and MOTS files (version 0x1E) the 64 x 256 light table follows the palette:
        ``rdColormap_LoadEntry`` (Engine/rdColormap.c in OpenJKDF2) reads it after the 768
        palette bytes, and the software rasterizers draw each texel's index through the row of
        its light level (Raster/rdZRaster.c). ``lightLevels`` keeps it. It is None for Grim
        Fandango files, whose layout after the palette is not sourced here, and for a file that
        ends before the table is complete; the palette still loads.
        """
        if isinstance(filePath, str):
            filePath = Path(filePath)
        if not filePath.is_file() or not filePath.exists():
            raise ImportError('Invalid cmp file path')

        with filePath.open('rb') as f:
            data = f.read(CmpHeader.format.size)
            if len(data) != CmpHeader.format.size:
                raise ImportError('Truncated CMP header')
            rh = CmpHeader.format.unpack(data)
            h = CmpHeader(*rh)
            if h.signature != file_magic:
                raise ImportError('Invalid CMP file')
            if h.version not in supported_versions:
                raise ImportError(f'Invalid CMP file version 0x{h.version:02x}')

            # Read palette
            pal = f.read(256 * 3) # 256 * len(CmpPaletteRGB)
            if len(pal) != 256 * 3:
                raise ImportError('Truncated CMP palette')
            cmp = cls()
            cmp.palette = [CmpPaletteRGB(*e) for e in np.frombuffer(pal, dtype=np.uint8).reshape(-1,3)]
            cmp.lightLevels = None
            if h.version == 0x1E:
                table = f.read(kCmpLightLevels * 256)
                if len(table) == kCmpLightLevels * 256:
                    cmp.lightLevels = np.frombuffer(table, dtype=np.uint8).reshape(kCmpLightLevels, 256).copy()
            return cmp
