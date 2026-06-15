ARG BASE_IMAGE=ubuntu:24.04
ARG UV_VERSION=0.10.12

# only way to get COPY from using a variable as the version is to use a multi stage build, we copy the uv binaries from the uv image to the final image
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv_source

FROM ${BASE_IMAGE} AS base_with_uv

LABEL description="Base ubuntu image with python installed with UV instead of the system one."
LABEL org.opencontainers.image.description="Base ubuntu image with python installed with UV instead of the system one."
LABEL org.opencontainers.image.authors="matteo.bruni@gmail.com"
LABEL org.opencontainers.image.source="https://github.com/matteo-bruni/gstreamer-oci"

ARG PYTHON_VERSION=3.12

# ENVIRONMENT VARIABLES
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# for debug symbols
# see https://ubuntu.com/server/docs/how-to/debugging/about-debuginfod/
ENV DEBUGINFOD_URLS="https://debuginfod.ubuntu.com"

# UV OPTIONS
ENV UV_COMPILE_BYTECODE=1
ENV UV_PROJECT_ENVIRONMENT=/opt/uv/venv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV VIRTUAL_ENV=/opt/uv/venv
ENV UV_CACHE_DIR=/opt/uv/cache/
ENV UV_TOOL_BIN_DIR=/opt/uv/bin/
ENV UV_RESOLUTION=highest
ENV UV_HTTP_TIMEOUT=300

# used to find the python toolchain install folder
ENV UV_TOOLCHAIN_DIR=/opt/uv/toolchain-${PYTHON_VERSION}

COPY --from=uv_source /uv /uvx /bin/

# install python version and install in VIRTUAL_ENV
RUN uv python install ${PYTHON_VERSION} --default && \
    uv venv ${VIRTUAL_ENV} && \
    # create custom toolchain dir with the version in the name
    ln -s $(${VIRTUAL_ENV}/bin/python -c "import sys; print(sys.base_prefix)") /opt/uv/toolchain-${PYTHON_VERSION} && \
    # add the python library to the ldconfig path so that the gstreamer build can find it
    echo "${UV_PYTHON_INSTALL_DIR}/cpython-${PYTHON_VERSION}-linux-x86_64-gnu/lib" > /etc/ld.so.conf.d/uv-python.conf && \
    ldconfig

# set uv python path as the default one and use it instead of the ubuntu provided one
ENV PATH=${VIRTUAL_ENV}/bin:${UV_TOOL_BIN_DIR}:${PATH}
# let meson and other libs to find the uv python installation using its pkg-config file
ENV PKG_CONFIG_PATH="${UV_TOOLCHAIN_DIR}/lib/pkgconfig"

FROM base_with_uv AS gstreamer_builder

# BUILD ARGS
ARG GSTREAMER_VERSION
ARG MESON_VERSION=1.5.2
ARG GSTREAMER_BUILD_TYPE=debug
# profile like base, full
ARG GSTREAMER_BUILD_PROFILE=base 

# GSTREAMER OPTIONS
ENV GSTREAMER_PATH=/opt/gstreamer
ENV GSTREAMER_ENABLE_NON_FREE=false
# Directory where gstreamer will be installed (other than default /usr) to be able to copied in later images
ENV GSTREAMER_INSTALL_DIR=/install/gstreamer
# where the wheel will be after compilation
ENV PY_WHEEL_DIR=/opt/wheel

# DEV dependencies for gstreamer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential ca-certificates \
        git curl wget \
        # gstreamer generic dependencies
        flex libunwind-dev libdw-dev libgmp-dev libglib2.0-dev \
        clang libclang-dev bison \
        # needed by cargo
        libssl-dev \
        libgl1-mesa-dev libegl1-mesa-dev libgles2-mesa-dev libdrm-dev libwayland-dev libx11-dev libgbm-dev \
        # needed for gst-python
        libgirepository1.0-dev libgirepository-2.0-dev \
        gir1.2-girepository-3.0-dev \
        libcairo2-dev gcc pkg-config libcairo2-dev \
        && \      
    if [ "${GSTREAMER_ENABLE_NON_FREE}" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            # drivers non-free dependencies
            intel-media-va-driver-non-free \
            # encoders
            x265 x264 libx265-dev libx264-dev; \
    fi && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Keep the Cargo bin dir on PATH even for non-full builds; the directory may
# not exist, which is harmless, and avoids needing conditional ENV handling.
# Also prepend the custom GStreamer install dir for tools produced during build.
ENV PATH=/root/.cargo/bin:${GSTREAMER_INSTALL_DIR}/bin:${PATH}

# Install python package dependencies
# jinja2 and pygments are needed for the gstreamer documentation build
# typogrify is needed for the gstreamer website build
RUN uv pip install --no-cache \
    cmake \
    meson==${MESON_VERSION} \
    ninja \
    Jinja2 \
    Pygments  \
    typogrify \
    setuptools

# RUST & CARGO-C INSTALLATION
# Only needed for the `full` profile, where gst-plugins-rs is enabled.
RUN if [ "${GSTREAMER_BUILD_PROFILE}" = "full" ]; then \
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal && \
        cargo install cargo-c && \
        rm -rf "$HOME/.cargo/registry" "$HOME/.cargo/git"; \
    fi

# build gstreamer from source
RUN mkdir -p ${GSTREAMER_PATH} && \
    git clone \
        --depth 1 --branch ${GSTREAMER_VERSION} \
        https://gitlab.freedesktop.org/gstreamer/gstreamer.git ${GSTREAMER_PATH} && \
    cd ${GSTREAMER_PATH} && \
    LTO_FLAG="" && \
    if [ "${GSTREAMER_BUILD_TYPE}" = "release" ]; then \
        LTO_FLAG="-Db_lto=true"; \
    else \
        apt-get update && \
        apt-get install -y --no-install-recommends gdb && \
        apt-get clean && rm -rf /var/lib/apt/lists/*; \
    fi && \
    NON_FREE="" && \
    if [ "${GSTREAMER_ENABLE_NON_FREE}" = "true" ]; then \
        NON_FREE="-Dnon-free=true"; \
    else \
        NON_FREE=""; \
    fi && \
    if [ "${GSTREAMER_BUILD_PROFILE}" = "full" ]; then \
        # full with libav and rust plugins
        MESON_FEATURES="-Dgood=enabled -Dbad=enabled -Dugly=enabled -Dlibav=enabled -Drs=enabled -Dgst-plugins-rs:csound=disabled"; \
    else \
        # base profile
        MESON_FEATURES="-Dgood=enabled -Dbad=enabled -Dugly=enabled -Dlibav=disabled"; \
    fi && \
    # configure with meson
    meson setup build \
        --prefix=/usr \
        --warnlevel=0 \
        --buildtype=${GSTREAMER_BUILD_TYPE} \
        ${LTO_FLAG} \
        ${NON_FREE} \
        -Dpackage-origin=https://gitlab.freedesktop.org/gstreamer/gstreamer.git \
        # explicitly select features
        # -Dauto_features=disabled \
        # BUG if disablin autofeatures will fail 
        -Ddoc=auto \
        # 1. just base
        -Dbase=enabled \
        ${MESON_FEATURES} \
        # rtsp
        -Drtsp_server=enabled \
        # python
        -Dpython=enabled \
        -Dintrospection=enabled \
        # 4. not required
        -Dgst-examples=disabled \
        -Dtests=disabled \
        # disable always
        -Dgst-plugins-rs:examples=disabled \
        # TODO: provide a `full` build variant with rust plugins enabled
        # # rust plugins (disable examples will cause error on 1.28.1 )
        # -Drs=enabled \
        # -Dgst-plugins-rs:gtk4=enabled \
        # # build gtk 4
        # -Dgtk=enabled \
        # -Dgst-plugins-bad:vulkan=enabled \
        # -Dgst-plugins-bad:vulkan-video=enabled \
        2>&1 | tee ${GSTREAMER_PATH}/build.log && \
    # actual build
    ninja -C build 2>&1 | tee -a ${GSTREAMER_PATH}/build.log && \
    # install everything but the gst-python binding, we will install it later after we build the python package
    # ninja -C build install 2>&1 | tee -a ${GSTREAMER_PATH}/build.log && \
    # also install in a custom install dir to be copied in later images
    DESTDIR=${GSTREAMER_INSTALL_DIR} ninja -C build install 2>&1 | tee -a ${GSTREAMER_PATH}/build.log && \
    rm -f /root/.cache/gstreamer-1.0/registry.x86_64.bin 

# create a python package for easier redistribution of the gstreamer python overrides
# the package will be called gst-python-binding to avoid name clash with gst-python
RUN --mount=type=bind,src=build-utils,target=/tmp/build-utils \
    # we will build the package in a separate directory to avoid polluting the gstreamer source tree
    export GST_PYTHON_BINDING_BUILD_DIR=${GSTREAMER_PATH}/gst-python-binding/ && \
    # install build dependencies
    uv pip install --no-cache \
        wheel \
        typing-extensions \
    && \
    # create the directory structure for the package
    mkdir -p "${GST_PYTHON_BINDING_BUILD_DIR}/src/gi/overrides" && \
    # move the pyproject.toml file and update the version to match the gstreamer version, this way we can publish the package on pypi if we want in the future
    cp \
        /tmp/build-utils/gst-python-binding/pyproject.toml \
        "${GST_PYTHON_BINDING_BUILD_DIR}/pyproject.toml" && \
    # update version in wheel (append +debug local tag for debug builds)
    if [ "${GSTREAMER_BUILD_TYPE}" = "debug" ]; then \
        GST_WHEEL_VERSION="${GSTREAMER_VERSION}+debug"; \
    else \
        GST_WHEEL_VERSION="${GSTREAMER_VERSION}"; \
    fi && \
    sed -i 's/version = ".*"/version = "'"${GST_WHEEL_VERSION}"'"/' "${GST_PYTHON_BINDING_BUILD_DIR}/pyproject.toml" && \
    # copy the compiled the .so files in the new package
    cp \
        ${GSTREAMER_PATH}/build/subprojects/gst-python/gi/overrides/_gi_gst.*.so \
        "${GST_PYTHON_BINDING_BUILD_DIR}/src/gi/overrides/" && \
    # copy the .py files in the new package
    cp \
        ${GSTREAMER_PATH}/subprojects/gst-python/gi/overrides/Gst*.py \
        "${GST_PYTHON_BINDING_BUILD_DIR}/src/gi/overrides/" && \
    cd "${GST_PYTHON_BINDING_BUILD_DIR}" && \
    # build the wheel
    uv build --wheel && \
    # the resulting wheel is not platform tagged so we use the wheel module to add the platform tag
    # see https://stackoverflow.com/questions/75204255/how-to-force-a-platform-wheel-using-build-and-pyproject-toml
    cd "${GST_PYTHON_BINDING_BUILD_DIR}/dist" && \
    PYTAG=$(python -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')") && \
    PLAT=$(python -c "import sysconfig; print(sysconfig.get_platform().replace('-','_').replace('.','_'))") && \
    # set the abi tag and the platform tag
    python -m wheel tags \
        --python-tag "${PYTAG}" \
        --abi-tag "${PYTAG}" \
        --platform-tag "${PLAT}" \
        --remove \
        gst_python_binding-*-py3-none-any.whl && \
    # copy wheel to wheel directory
    mkdir -p ${PY_WHEEL_DIR} && \
    cp "${GST_PYTHON_BINDING_BUILD_DIR}/dist/"*.whl "${PY_WHEEL_DIR}/" && \
    # install
    uv pip install --no-cache ${PY_WHEEL_DIR}/gst_python_binding-*.whl && \
    # Build dependency wheels here so the final image does not need a compiler
    # toolchain when installing gst_python_binding and its Python deps.
    uv run --with pip pip wheel \
        --wheel-dir ${PY_WHEEL_DIR} \
        --no-binary pycairo,PyGObject \
        pycairo \
        PyGObject


FROM base_with_uv AS final

LABEL description="Gstreamer built with meson to create gst-python wheel bindings package"
LABEL org.opencontainers.image.description="Gstreamer built with meson to create gst-python wheel bindings package"
LABEL org.opencontainers.image.authors="matteo.bruni@gmail.com"
LABEL org.opencontainers.image.source="https://github.com/matteo-bruni/gstreamer-oci"

ARG GSTREAMER_BUILD_PROFILE=base
ARG GSTREAMER_ENABLE_NON_FREE=false

# TODO: to make it work across ubuntu version we can do something like
# APT_PKGS="$APT_PKGS ^libavcodec[0-9]+$ ^libavformat[0-9]+$ ^libavutil[0-9]+$"

# 1. INSTALL RUNTIME DEPENDENCIES (Apt-get)
# TODO: filter 
RUN apt-get update && \
    EXTRA_APT_PKGS="" && \
    if [ "${GSTREAMER_ENABLE_NON_FREE}" = "true" ]; then \
        EXTRA_APT_PKGS="$APT_PKGS libx264-164 libx265-199 intel-media-va-driver-non-free"; \
    fi && \
    # if [ "${GSTREAMER_BUILD_PROFILE}" = "full" ]; then \
    #     # put here extra deps needed for full
    #     # EXTRA_APT_PKGS="$EXTRA_APT_PKGS <package> <package>"; \
    # fi && \
    apt-get install -y --no-install-recommends \
        curl ca-certificates xz-utils \
        liba52-0.7.4 \
        libaa1 \
        libaom3 \
        libasound2t64 \
        libass9 \
        libavfilter9 \
        libbs2b0 \
        libcaca0 \
        libcairo-gobject2 \
        libcairo2 \
        libcairo-script-interpreter2 \
        libchromaprint1 \
        libcurl3t64-gnutls \
        libdca0 \
        libde265-0 \
        libdrm2 \
        libdv4t64 \
        libdvdnav4 \
        libdvdread8t64 \
        libegl1 \
        libfaac0 \
        libfaad2 \
        libfdk-aac2 \
        libflac12t64 \
        libflite1 \
        libfluidsynth3 \
        libgdk-pixbuf-2.0-0 \
        libgme0 \
        libgsm1 \
        libgtk-3-0t64 \
        libgudev-1.0-0 \
        libjpeg-turbo8 \
        liblcms2-2 \
        liblilv-0-0 \
        libmfx1 \
        libmjpegutils-2.1-0t64 \
        libmodplug1 \
        libmp3lame0 \
        libmpcdec6 \
        libmpeg2-4 \
        libmpg123-0t64 \
        libogg0 \
        libopencore-amrnb0 \
        libopencore-amrwb0 \
        libopenexr-3-1-30 \
        libopenjp2-7 \
        libopus0 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpng16-16t64 \
        libpulse0 \
        # libpython3.12t64 \
        librsvg2-2 \
        libsbc1 \
        libshout3 \
        libsndfile1 \
        libsoundtouch1 \
        libspandsp2t64 \
        libspeex1 \
        libsrt1.5-gnutls \
        libsrtp2-1 \
        libtag1v5-vanilla \
        libtheora0 \
        libtwolame0 \
        libva2 \
        libvisual-0.4-0 \
        libvo-aacenc0 \
        libvo-amrwbenc0 \
        libvorbis0a \
        libvpx9 \
        libwavpack1 \
        libwebp7 \
        libwebpdemux2 \
        libwildmidi2 \
        libx11-6 \
        libzbar0t64 \
        libzvbi0t64 \
        libglib2.0-0t64 \
        libunwind8 \
        libdw1t64 \
        libmpeg2encpp-2.1-0t64 \
        libxv1 \
        libmplex2-2.1-0t64 \
        libgirepository-1.0-1 \
        libgirepository-2.0-0 \
        gir1.2-glib-2.0 \
        libxtst6 \
        # wayland dependencies
        libwayland-client0 \
        wayland-protocols \
        # TODO: remove if non free
        # non-free dependencies
        $APT_PKGS \
        # TODO: provide installation a `full` build variant
        # frei0r-plugins \
        # # used for wayland support with vulkan
        # libglx0 libwayland-client0 \
        && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. COPY COMPILED BINARIES FROM BUILDERS
# COPY GStreamer installation from the GStreamer builder 
# (using DESTDIR=/install/gstreamer in the GStreamer builder)
COPY --from=gstreamer_builder /install/gstreamer/ /


# install the wheel previously built.
# pygobject and pycairo need gcc so we use the already built wheels
RUN --mount=type=bind,from=gstreamer_builder,source=/opt/wheel,target=/tmp/wheel \
    uv pip install --no-cache \
        typing-extensions \
        /tmp/wheel/pycairo-*.whl \
        /tmp/wheel/pygobject-*.whl && \
    uv pip install --no-cache --no-deps \
        /tmp/wheel/gst_python_binding-*.whl && \
    # Keep prebuilt wheels in the final image so release tooling can extract them
    # after the image is built and before publishing release assets.
    mkdir -p /opt/wheel && \
    cp /tmp/wheel/gst_python_binding-*.whl /opt/wheel/