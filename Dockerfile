ARG BASE_IMAGE=ubuntu:24.04
ARG UV_VERSION=0.10.12

# only way to get COPY from using a variable as the version is to use a multi stage build, we copy the uv binaries from the uv image to the final image
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv_source

FROM ${BASE_IMAGE}

# install curl to handle custom python version
COPY --from=uv_source /uv /uvx /bin/

LABEL description="Gstreamer built with meson to create gst-python wheel bindings package"

LABEL org.opencontainers.image.description="Gstreamer built with meson to create gst-python wheel bindings package"
LABEL org.opencontainers.image.authors="matteo.bruni@gmail.com"

# BUILD ARGS
ARG GSTREAMER_VERSION
ARG MESON_VERSION=1.5.2
ARG PYTHON_VERSION=3.12
ARG GSTREAMER_BUILD_TYPE=debug

# ENVIRONMENT VARIABLES
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# GSTREAMER OPTIONS
ENV GSTREAMER_PATH=/opt/gstreamer
# where the wheel will be after compilation
ENV PY_WHEEL_DIR=/opt/wheel

# UV OPTIONS
ENV UV_COMPILE_BYTECODE=1
ENV UV_PROJECT_ENVIRONMENT=/opt/uv/venv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
ENV VIRTUAL_ENV=/opt/uv/venv
ENV UV_CACHE_DIR=/opt/uv/cache/
ENV UV_TOOL_BIN_DIR=/opt/uv/bin/
ENV UV_RESOLUTION=highest
ENV UV_HTTP_TIMEOUT=300

# DEV dependencies for gstreamer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential ca-certificates \
        git curl wget \
        # gstreamer dependencies
        flex libunwind-dev libdw-dev libgmp-dev libglib2.0-dev \
        clang libclang-dev bison \
        # needed for gst-python
        libgirepository1.0-dev libgirepository-2.0-dev \
        gir1.2-girepository-3.0-dev \
    && \      
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# used to find the python toolchain install folder
ENV UV_TOOLCHAIN_DIR=/opt/uv/toolchain-${PYTHON_VERSION}
# install python version and install in VIRTUAL_ENV
RUN uv python install ${PYTHON_VERSION} --default && \
    uv venv ${VIRTUAL_ENV} --python ${PYTHON_VERSION} && \
    ln -s $(${VIRTUAL_ENV}/bin/python -c "import sys; print(sys.base_prefix)") /opt/uv/toolchain-${PYTHON_VERSION} && \
    # add the python library to the ldconfig path so that the gstreamer build can find it
    echo "${UV_TOOLCHAIN_DIR}/lib" > /etc/ld.so.conf.d/uv-python.conf && \
    ldconfig

# let meson and other libs to find the uv python installation using its pkg-config file
ENV PKG_CONFIG_PATH="${UV_TOOLCHAIN_DIR}/lib/pkgconfig"
# will autodetect the python installed in the VENV and use it instead of the ubuntu provided one
ENV PATH=${VIRTUAL_ENV}/bin:${PATH}

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
    setuptools \
    wheel \
    typing-extensions

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
        # also install gdb and debug symbols in debug builds
        apt-get update && \
        apt-get install -y --no-install-recommends gdb ubuntu-dbgsym-keyring && \
        echo "deb http://ddebs.ubuntu.com $(. /etc/os-release && echo $VERSION_CODENAME) main restricted universe multiverse" > /etc/apt/sources.list.d/ddebs.list && \
        echo "deb http://ddebs.ubuntu.com $(. /etc/os-release && echo $VERSION_CODENAME)-updates main restricted universe multiverse" >> /etc/apt/sources.list.d/ddebs.list && \
        apt-get update && \
        apt-get install -y --no-install-recommends \
            libglib2.0-0t64-dbgsym && \
        apt-get clean && \
        rm -rf /var/lib/apt/lists/*; \
    fi && \
    meson setup build \
        --prefix=/usr \
        --warnlevel=0 \
        --buildtype=${GSTREAMER_BUILD_TYPE} \
        ${LTO_FLAG} \
        -Dpackage-origin=https://gitlab.freedesktop.org/gstreamer/gstreamer.git \
        -Dpython=enabled \
        -Dintrospection=enabled \
        2>&1 | tee ${GSTREAMER_PATH}/build.log && \
    # actual build
    ninja -C build 2>&1 | tee -a ${GSTREAMER_PATH}/build.log && \
    # install everythin but the gst-python binding, we will install it later after we build the python package
    ninja -C build install 2>&1 | tee -a ${GSTREAMER_PATH}/build.log && \
    rm -f /root/.cache/gstreamer-1.0/registry.x86_64.bin 

# create a python package for easier redistribution of the gstreamer python overrides
# the package will be called gst-python-binding to avoid name clash with gst-python
RUN --mount=type=bind,src=build-utils,target=/tmp/build-utils \
    # we will build the package in a separate directory to avoid polluting the gstreamer source tree
    export GST_PYTHON_BINDING_BUILD_DIR=${GSTREAMER_PATH}/gst-python-binding/ && \
    # create the directory structure for the package
    mkdir -p "${GST_PYTHON_BINDING_BUILD_DIR}/src/gi/overrides" && \
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
        ${GSTREAMER_PATH}/build/subprojects/gst-python/gi/overrides/_gi_gst*.so \
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
    find ${GSTREAMER_PATH} -name "*.whl" -exec cp {} ${PY_WHEEL_DIR} \; && \
    # install
    uv pip install --no-cache ${PY_WHEEL_DIR}/gst_python_binding-*.whl
