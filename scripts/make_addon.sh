#!/bin/sh
# Compatibility entry point. Build outputs must be outside every Git checkout.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${PYTHON:-python3}" "$script_dir/build_extension.py" "$@"
