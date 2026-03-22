# Allow for positional arguments in Just receipes.

set positional-arguments := true

# Default recipe that runs if you type "just".
default:
    just --list


# build image, will reopen last layer with shell on building failure
build GSTREAMER_BUILD_TYPE BASE_IMAGE BASE_TAG GSTREAMER_VERSION PYTHON_VERSION UV_VERSION:
    #!/usr/bin/env bash
    set -euo pipefail

    BASE_IMAGE={{ BASE_IMAGE }}
    BASE_TAG={{ BASE_TAG }}
    GSTREAMER_VERSION={{ GSTREAMER_VERSION }}
    PYTHON_VERSION={{ PYTHON_VERSION }}
    UV_VERSION={{ UV_VERSION }}
    GSTREAMER_BUILD_TYPE={{ GSTREAMER_BUILD_TYPE }}

    # to enable buildx debug mode, we need to set the experimental flag
    export BUILDX_EXPERIMENTAL=1

    echo "Building debug image with"
    echo " - BASE_IMAGE=${BASE_IMAGE}"
    echo " - BASE_TAG=${BASE_TAG}"
    echo " - GSTREAMER_VERSION=${GSTREAMER_VERSION}"
    echo " - PYTHON_VERSION=${PYTHON_VERSION}"
    echo " - UV_VERSION=${UV_VERSION}"
    echo " - GSTREAMER_BUILD_TYPE=${GSTREAMER_BUILD_TYPE}"
    # tag suffix: only add -debug for debug builds
    TAG_SUFFIX=""
    if [ "${GSTREAMER_BUILD_TYPE}" != "release" ]; then TAG_SUFFIX="-${GSTREAMER_BUILD_TYPE}"; fi

    TAG="gstreamer:gst-${GSTREAMER_VERSION}-${BASE_IMAGE}.${BASE_TAG}-py-${PYTHON_VERSION}${TAG_SUFFIX}"

    BUILD_ARGS=(
        --build-arg BASE_IMAGE=${BASE_IMAGE}:${BASE_TAG}
        --build-arg GSTREAMER_VERSION=${GSTREAMER_VERSION}
        --build-arg PYTHON_VERSION=${PYTHON_VERSION}
        --build-arg UV_VERSION=${UV_VERSION}
        --build-arg GSTREAMER_BUILD_TYPE=${GSTREAMER_BUILD_TYPE}
        --progress auto
        --tag "${TAG}"
        .
    )

    # use buildx debug if available, otherwise fall back to plain docker build
    if docker buildx debug --help &>/dev/null; then
        export BUILDX_EXPERIMENTAL=1
        docker buildx debug --on=error --invoke=/bin/bash build "${BUILD_ARGS[@]}"
    else
        echo "Note: buildx debug not available, using plain docker build"
        docker build "${BUILD_ARGS[@]}"
    fi

build-debug BASE_IMAGE="ubuntu" BASE_TAG="24.04" GSTREAMER_VERSION="1.28.1" PYTHON_VERSION="3.12" UV_VERSION="latest": (build "debug" BASE_IMAGE BASE_TAG GSTREAMER_VERSION PYTHON_VERSION UV_VERSION)

build-release BASE_IMAGE="ubuntu" BASE_TAG="24.04" GSTREAMER_VERSION="1.28.1" PYTHON_VERSION="3.12" UV_VERSION="latest": (build "release" BASE_IMAGE BASE_TAG GSTREAMER_VERSION PYTHON_VERSION UV_VERSION)

# run container with example plugin mounted and gst-inspect it
run-example BASE_IMAGE="ubuntu" BASE_TAG="24.04" GSTREAMER_VERSION="1.28.1" PYTHON_VERSION="3.12":
    #!/usr/bin/env bash
    set -euo pipefail

    IMAGE="gstreamer:gst-{{ GSTREAMER_VERSION }}-{{ BASE_IMAGE }}.{{ BASE_TAG }}-py-{{ PYTHON_VERSION }}-debug"

    if ! docker image inspect "${IMAGE}" &>/dev/null; then
        echo "Image '${IMAGE}' not found. Build it first with:"
        echo ""
        echo "  just build-debug {{ BASE_IMAGE }} {{ BASE_TAG }} {{ GSTREAMER_VERSION }} {{ PYTHON_VERSION }}"
        echo ""
        exit 1
    fi

    docker run --rm -it \
        -v "$(pwd)/example_gst_plugin:/tmp/gst/python:ro" \
        -e GST_PLUGIN_PATH=/tmp/gst \
        "${IMAGE}" \
        bash
    # bash -c 'rm -f ~/.cache/gstreamer-1.0/registry.x86_64.bin && gst-inspect-1.0 python && exec bash'
