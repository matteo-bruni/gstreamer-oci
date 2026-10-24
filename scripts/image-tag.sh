#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
validate_build_args "$@"
image_tag
