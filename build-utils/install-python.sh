#!/usr/bin/env bash
set -euo pipefail

version="$1"
venv="$2"
uv python install "$version" --default
uv venv --python "$version" "$venv"
"$venv/bin/python" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))' \
    > "/etc/ld.so.conf.d/uv-$(basename "$venv").conf"
ldconfig
