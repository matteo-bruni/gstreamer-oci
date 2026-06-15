# Allow for positional arguments in Just receipes.

set positional-arguments := true

# Default recipe that runs if you type "just".
default:
    just --list


image-tag GSTREAMER_BUILD_TYPE BASE_IMAGE BASE_TAG GSTREAMER_VERSION GSTREAMER_BUILD_PROFILE GSTREAMER_ENABLE_NON_FREE PYTHON_VERSION:
    #!/usr/bin/env bash
    set -euo pipefail

    GSTREAMER_BUILD_TYPE={{ GSTREAMER_BUILD_TYPE }}
    BASE_IMAGE={{ BASE_IMAGE }}
    BASE_TAG={{ BASE_TAG }}
    GSTREAMER_VERSION={{ GSTREAMER_VERSION }}
    GSTREAMER_BUILD_PROFILE={{ GSTREAMER_BUILD_PROFILE }}
    GSTREAMER_ENABLE_NON_FREE={{ GSTREAMER_ENABLE_NON_FREE }}
    PYTHON_VERSION={{ PYTHON_VERSION }}

    TAG_SUFFIX=""
    if [ "${GSTREAMER_BUILD_TYPE}" != "release" ]; then TAG_SUFFIX="-${GSTREAMER_BUILD_TYPE}"; fi

    NON_FREE_SUFFIX=""
    if [ "${GSTREAMER_ENABLE_NON_FREE}" = "true" ]; then NON_FREE_SUFFIX="-non-free"; fi

    echo "gstreamer:${GSTREAMER_VERSION}-${BASE_IMAGE}.${BASE_TAG}-${GSTREAMER_BUILD_PROFILE}-py-${PYTHON_VERSION}${TAG_SUFFIX}${NON_FREE_SUFFIX}"


# build image, will reopen last layer with shell on building failure
build GSTREAMER_BUILD_TYPE BASE_IMAGE BASE_TAG GSTREAMER_VERSION GSTREAMER_BUILD_PROFILE GSTREAMER_ENABLE_NON_FREE PYTHON_VERSION UV_VERSION:
    #!/usr/bin/env bash
    set -euo pipefail

    BASE_IMAGE={{ BASE_IMAGE }}
    BASE_TAG={{ BASE_TAG }}
    GSTREAMER_VERSION={{ GSTREAMER_VERSION }}
    GSTREAMER_BUILD_PROFILE={{ GSTREAMER_BUILD_PROFILE }}
    GSTREAMER_ENABLE_NON_FREE={{ GSTREAMER_ENABLE_NON_FREE }}
    PYTHON_VERSION={{ PYTHON_VERSION }}
    UV_VERSION={{ UV_VERSION }}
    GSTREAMER_BUILD_TYPE={{ GSTREAMER_BUILD_TYPE }}

    # to enable buildx debug mode, we need to set the experimental flag
    export BUILDX_EXPERIMENTAL=1

    echo "Building debug image with"
    echo " - BASE_IMAGE=${BASE_IMAGE}"
    echo " - BASE_TAG=${BASE_TAG}"
    echo " - GSTREAMER_VERSION=${GSTREAMER_VERSION}"
    echo " - GSTREAMER_BUILD_PROFILE=${GSTREAMER_BUILD_PROFILE}"
    echo " - GSTREAMER_ENABLE_NON_FREE=${GSTREAMER_ENABLE_NON_FREE}"
    echo " - PYTHON_VERSION=${PYTHON_VERSION}"
    echo " - UV_VERSION=${UV_VERSION}"
    echo " - GSTREAMER_BUILD_TYPE=${GSTREAMER_BUILD_TYPE}"
    TAG="$(just image-tag "${GSTREAMER_BUILD_TYPE}" "${BASE_IMAGE}" "${BASE_TAG}" "${GSTREAMER_VERSION}" "${GSTREAMER_BUILD_PROFILE}" "${GSTREAMER_ENABLE_NON_FREE}" "${PYTHON_VERSION}")"

    BUILD_ARGS=(
        --build-arg "BASE_IMAGE=${BASE_IMAGE}:${BASE_TAG}"
        --build-arg "GSTREAMER_VERSION=${GSTREAMER_VERSION}"
        --build-arg "GSTREAMER_BUILD_PROFILE=${GSTREAMER_BUILD_PROFILE}"
        --build-arg "GSTREAMER_ENABLE_NON_FREE=${GSTREAMER_ENABLE_NON_FREE}"
        --build-arg "PYTHON_VERSION=${PYTHON_VERSION}"
        --build-arg "UV_VERSION=${UV_VERSION}"
        --build-arg "GSTREAMER_BUILD_TYPE=${GSTREAMER_BUILD_TYPE}"
        --progress auto
        --tag "${TAG}"
        --target final
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

build-debug BASE_IMAGE="ubuntu" BASE_TAG="24.04" GSTREAMER_VERSION="1.28.3" GSTREAMER_BUILD_PROFILE="base" GSTREAMER_ENABLE_NON_FREE="false" PYTHON_VERSION="3.12" UV_VERSION="latest": (build "debug" BASE_IMAGE BASE_TAG GSTREAMER_VERSION GSTREAMER_BUILD_PROFILE GSTREAMER_ENABLE_NON_FREE PYTHON_VERSION UV_VERSION)

build-release BASE_IMAGE="ubuntu" BASE_TAG="24.04" GSTREAMER_VERSION="1.28.3" GSTREAMER_BUILD_PROFILE="base" GSTREAMER_ENABLE_NON_FREE="false" PYTHON_VERSION="3.12" UV_VERSION="latest": (build "release" BASE_IMAGE BASE_TAG GSTREAMER_VERSION GSTREAMER_BUILD_PROFILE GSTREAMER_ENABLE_NON_FREE PYTHON_VERSION UV_VERSION)

# run container with example plugin mounted and gst-inspect it
run-example BASE_IMAGE="ubuntu" BASE_TAG="24.04" GSTREAMER_VERSION="1.28.3" GSTREAMER_BUILD_PROFILE="base" GSTREAMER_ENABLE_NON_FREE="false" PYTHON_VERSION="3.12":
    #!/usr/bin/env bash
    set -euo pipefail

    BASE_IMAGE={{ BASE_IMAGE }}
    BASE_TAG={{ BASE_TAG }}
    GSTREAMER_VERSION={{ GSTREAMER_VERSION }}
    GSTREAMER_BUILD_PROFILE={{ GSTREAMER_BUILD_PROFILE }}
    GSTREAMER_ENABLE_NON_FREE={{ GSTREAMER_ENABLE_NON_FREE }}
    PYTHON_VERSION={{ PYTHON_VERSION }}

    IMAGE="$(just image-tag debug "${BASE_IMAGE}" "${BASE_TAG}" "${GSTREAMER_VERSION}" "${GSTREAMER_BUILD_PROFILE}" "${GSTREAMER_ENABLE_NON_FREE}" "${PYTHON_VERSION}")"

    if ! docker image inspect "${IMAGE}" &>/dev/null; then
        echo "Image '${IMAGE}' not found. Build it first with:"
        echo ""
        echo "  just build-debug {{ BASE_IMAGE }} {{ BASE_TAG }} {{ GSTREAMER_VERSION }} {{ GSTREAMER_BUILD_PROFILE }} {{ GSTREAMER_ENABLE_NON_FREE }} {{ PYTHON_VERSION }}"
        echo ""
        exit 1
    fi

    docker run --rm -it \
        -v "$(pwd)/example_gst_plugin:/tmp/gst/python:ro" \
        -e GST_PLUGIN_PATH=/tmp/gst \
        "${IMAGE}" \
        bash
    # bash -c 'rm -f ~/.cache/gstreamer-1.0/registry.x86_64.bin && gst-inspect-1.0 python && exec bash'
