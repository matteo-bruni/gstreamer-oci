#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
cd "$(dirname "$0")/.."
[[ $# == 8 ]] || exit 1
validate_build_args "$8" "${@:1:7}"
image="$(image_tag)"
docker image inspect "$image" >/dev/null
docker run \
    --rm \
    -it \
    -v "$(pwd)/example_gst_plugin:/tmp/gst/python:ro" \
    -e GST_PLUGIN_PATH=/tmp/gst \
    "$image" bash
