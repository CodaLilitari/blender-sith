# SPDX-License-Identifier: GPL-3.0-or-later
"""Native model and animation audits for JK/MotS characters, without Blender.

``pose`` reproduces how the engine poses a 3DO hierarchy with KEY tracks. The other audit
modules build on it to find what a still model cannot show: joints that open, pieces left
behind by a cut, feet in the floor, pops between KEY entries. Every audit reads native files or
the add-on's resource objects and returns plain data; none of them edits its inputs.
"""
