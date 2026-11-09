# KEY export corrections in 1.0.3

An animation that returns a keyed location or rotation component to zero now exports that zero. Previously the coordinate-fill step treated an explicit zero as absent and retained the preceding value.

Quaternion tracks now choose equivalent pitch/yaw/roll values continuously between exported keyframes before calculating native KEY differentials. A sampled turn through 180 degrees therefore follows the short intended interval instead of introducing a roughly 340-degree reverse interval. Explicit Euler winding is preserved, including deliberately sparse multi-turn Euler keys.

Quaternion samples cannot uniquely encode a rotation of more than 180 degrees between samples. Key or bake enough samples to express the intended direction; this correction does not infer missing revolutions or bake Blender constraints, modifiers, or interpolation curves.

Regression coverage checks zero-return location and rotation in Euler and quaternion modes, two full turns around every native PYR axis, native differential interpolation at quarter/half/three-quarter sample times, and preservation of a sparse explicit 720-degree Euler turn. The background suite validates serialized files; game playback remains a separate test.
