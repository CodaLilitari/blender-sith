#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the existing app suite with isolated settings and a process deadline."""
import argparse

import run_regression as runner


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default="blender", help="Blender executable path or command")
    parser.add_argument("--timeout", type=runner.positive_seconds, default=600)
    args = parser.parse_args(argv)
    command = [args.blender, "--background", "--factory-startup", "--offline-mode",
               "--disable-autoexec", "--threads", "2", "--python-exit-code", "1",
               "--python", str(runner.ROOT / "tests" / "run.py"), "--", "--suite", "app"]
    return runner.run_isolated(command, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
