#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
[[ $# == 9 ]] || { echo 'Expected 9 build options' >&2; exit 1; }
validate_build_args "${@:1:8}"
uv_version="$9"
[[ "$uv_version" =~ ^([0-9]+\.[0-9]+\.[0-9]+|latest)$ ]] || { echo 'Invalid uv version' >&2; exit 1; }
[[ "${BUILD_JOBS:-6}" =~ ^[1-9][0-9]*$ ]] || { echo 'BUILD_JOBS must be positive' >&2; exit 1; }
check_gstreamer_tag

target=final
[[ "$IMAGE_TARGET" == runtime ]] || target=gstreamer_builder
args=(
    --platform linux/amd64
    --file "$root/docker/ubuntu-$BASE_TAG.Dockerfile"
    --build-arg "BASE_IMAGE=$BASE_IMAGE:$BASE_TAG"
    --build-arg "UV_VERSION=$uv_version"
    --build-arg "GSTREAMER_VERSION=$GSTREAMER_VERSION"
    --build-arg "PYTHON_VERSION=$PYTHON_VERSION"
    --build-arg "GSTREAMER_BUILD_TYPE=$GSTREAMER_BUILD_TYPE"
    --build-arg "GSTREAMER_BUILD_PROFILE=$GSTREAMER_BUILD_PROFILE"
    --build-arg "VARIANT=$VARIANT"
    --build-arg "BUILD_JOBS=${BUILD_JOBS:-6}"
    --progress "${BUILD_PROGRESS:-auto}"
    --tag "$(image_tag)"
    --target "$target"
)
[[ -z "${BUILD_CACHE_FROM:-}" ]] || args+=(--cache-from "$BUILD_CACHE_FROM")
[[ -z "${BUILD_CACHE_TO:-}" ]] || args+=(--cache-to "$BUILD_CACHE_TO")
docker buildx build --load "${args[@]}" "$root"
