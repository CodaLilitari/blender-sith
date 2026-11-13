#!/bin/sh
# Preserve the fork's explicit deterministic flags and upstream's ZIP-file CLI.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for argument in "$@"; do
    case "$argument" in
        --output-dir|--output-dir=*|--source-dir|--source-dir=*)
            exec "${PYTHON:-python3}" "$script_dir/build_extension.py" "$@"
            ;;
    esac
done
exec "${PYTHON:-python3}" "$script_dir/make_addon.py" "$@"
