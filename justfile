set positional-arguments := true

default:
    just --list

image-tag \
    GSTREAMER_BUILD_TYPE \
    BASE_IMAGE \
    BASE_TAG \
    GSTREAMER_VERSION \
    GSTREAMER_BUILD_PROFILE \
    VARIANT \
    IMAGE_TARGET \
    PYTHON_VERSION:
    #!/usr/bin/env bash
    exec bash scripts/image-tag.sh "$@"

# Build and load an image: VARIANT=free/non-free, IMAGE_TARGET=runtime/dev.
build \
    GSTREAMER_BUILD_TYPE \
    BASE_IMAGE \
    BASE_TAG \
    GSTREAMER_VERSION \
    GSTREAMER_BUILD_PROFILE \
    VARIANT \
    IMAGE_TARGET \
    PYTHON_VERSION \
    UV_VERSION:
    #!/usr/bin/env bash
    exec bash scripts/build.sh "$@"

build-debug \
    BASE_IMAGE="ubuntu" \
    BASE_TAG="24.04" \
    GSTREAMER_VERSION="1.28.8" \
    GSTREAMER_BUILD_PROFILE="base" \
    VARIANT="free" \
    IMAGE_TARGET="runtime" \
    PYTHON_VERSION="3.12" \
    UV_VERSION="0.13.5": (build \
        "debug" \
        BASE_IMAGE \
        BASE_TAG \
        GSTREAMER_VERSION \
        GSTREAMER_BUILD_PROFILE \
        VARIANT \
        IMAGE_TARGET \
        PYTHON_VERSION \
        UV_VERSION)

build-debugoptimized \
    BASE_IMAGE="ubuntu" \
    BASE_TAG="24.04" \
    GSTREAMER_VERSION="1.28.8" \
    GSTREAMER_BUILD_PROFILE="base" \
    VARIANT="free" \
    IMAGE_TARGET="runtime" \
    PYTHON_VERSION="3.12" \
    UV_VERSION="0.13.5": (build \
        "debugoptimized" \
        BASE_IMAGE \
        BASE_TAG \
        GSTREAMER_VERSION \
        GSTREAMER_BUILD_PROFILE \
        VARIANT \
        IMAGE_TARGET \
        PYTHON_VERSION \
        UV_VERSION)

build-release \
    BASE_IMAGE="ubuntu" \
    BASE_TAG="24.04" \
    GSTREAMER_VERSION="1.28.8" \
    GSTREAMER_BUILD_PROFILE="base" \
    VARIANT="free" \
    IMAGE_TARGET="runtime" \
    PYTHON_VERSION="3.12" \
    UV_VERSION="0.13.5": (build \
        "release" \
        BASE_IMAGE \
        BASE_TAG \
        GSTREAMER_VERSION \
        GSTREAMER_BUILD_PROFILE \
        VARIANT \
        IMAGE_TARGET \
        PYTHON_VERSION \
        UV_VERSION)

run-example \
    BASE_IMAGE="ubuntu" \
    BASE_TAG="24.04" \
    GSTREAMER_VERSION="1.28.8" \
    GSTREAMER_BUILD_PROFILE="base" \
    VARIANT="free" \
    IMAGE_TARGET="runtime" \
    PYTHON_VERSION="3.12" \
    GSTREAMER_BUILD_TYPE="release":
    #!/usr/bin/env bash
    exec bash scripts/run-example.sh "$@"

# Run container and clean-venv checks against an already built image.
verify-image IMAGE:
    #!/usr/bin/env bash
    exec bash scripts/verify-image.sh "$1"
