#!/usr/bin/env bash

validate_build_args() {
    RELEASE_REVISION=${RELEASE_REVISION:-1}
    [[ "$RELEASE_REVISION" =~ ^[1-9][0-9]*$ ]] || { echo 'RELEASE_REVISION must be a positive integer without leading zeros' >&2; return 1; }
    [[ $# == 8 ]] || { echo 'Expected 8 build options' >&2; return 1; }
    GSTREAMER_BUILD_TYPE="$1" BASE_IMAGE="$2" BASE_TAG="$3" GSTREAMER_VERSION="$4"
    GSTREAMER_BUILD_PROFILE="$5" VARIANT="$6" IMAGE_TARGET="$7" PYTHON_VERSION="$8"
    case "$GSTREAMER_BUILD_TYPE" in release|debug|debugoptimized) ;; *) echo 'Invalid build type' >&2; return 1;; esac
    [[ "$BASE_IMAGE" == ubuntu && "$BASE_TAG" =~ ^(24\.04|26\.04)$ ]] || { echo 'Supported bases: ubuntu 24.04, ubuntu 26.04' >&2; return 1; }
    [[ "$GSTREAMER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'GStreamer must be a numeric release tag (for example 1.28.8)' >&2; return 1; }
    case "$GSTREAMER_BUILD_PROFILE" in minimal|base|full) ;; *) echo 'Invalid build profile' >&2; return 1;; esac
    # Accept legacy positional booleans while keeping canonical values descriptive.
    case "$VARIANT" in
        free|false) VARIANT=free ;;
        non-free|true) VARIANT=non-free ;;
        *) echo 'Variant must be free or non-free' >&2; return 1 ;;
    esac
    case "$IMAGE_TARGET" in
        runtime|false) IMAGE_TARGET=runtime ;;
        dev|true) IMAGE_TARGET=dev ;;
        *) echo 'Image target must be runtime or dev' >&2; return 1 ;;
    esac
    [[ "$PYTHON_VERSION" =~ ^3\.([0-9]+)(\.[0-9]+)?$ && "${BASH_REMATCH[1]}" -ge 12 ]] || { echo 'Python must be 3.12 or newer' >&2; return 1; }
}

image_tag() {
    local suffix=''
    [[ "$GSTREAMER_BUILD_TYPE" == release ]] || suffix+="-$GSTREAMER_BUILD_TYPE"
    [[ "$IMAGE_TARGET" == runtime ]] || suffix+='-dev'
    [[ "$VARIANT" == free ]] || suffix+='-non-free'
    suffix+="$(revision_suffix)"
    printf 'gstreamer:%s-%s.%s-%s-py-%s%s\n' "$GSTREAMER_VERSION" "$BASE_IMAGE" "$BASE_TAG" "$GSTREAMER_BUILD_PROFILE" "$PYTHON_VERSION" "$suffix"
}

revision_suffix() {
    [[ "$RELEASE_REVISION" == 1 ]] || printf '%s' "-r$RELEASE_REVISION"
    return 0
}

image_alias_tag() {
    local RELEASE_REVISION=1
    printf '%s-latest\n' "$(image_tag)"
}

check_gstreamer_tag() {
    git ls-remote \
        --exit-code \
        --tags \
        https://gitlab.freedesktop.org/gstreamer/gstreamer.git \
        "refs/tags/$GSTREAMER_VERSION" >/dev/null
}
