# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ubuntu:26.04
ARG UV_VERSION=0.13.5
# 1. UV BINARIES
# COPY --from cannot interpolate an image tag directly; this stage selects uv once.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv_source

# 2. COMMON UBUNTU BASE
# Shared environment and uv binaries, without a Python version or compiler toolchain.
FROM ${BASE_IMAGE} AS os_base
# Stop on command/pipeline failures, including commands whose output is piped to tee.
SHELL ["/bin/bash", "-e", "-o", "pipefail", "-c"]
LABEL description="GStreamer compiled from source with Python bindings and prebuilt wheels."
LABEL org.opencontainers.image.description="GStreamer compiled from source with Python bindings and prebuilt wheels."
LABEL org.opencontainers.image.source="https://github.com/matteo-bruni/gstreamer-oci"
LABEL org.opencontainers.image.authors="matteo.bruni@gmail.com"
# Ubuntu debug-symbol lookup for tools supporting debuginfod, such as gdb/elfutils.
ENV DEBUGINFOD_URLS="https://debuginfod.ubuntu.com"
ENV \
    # Avoid interactive APT prompts.
    DEBIAN_FRONTEND=noninteractive \
    # Avoid implicit bytecode writes when running Python.
    PYTHONDONTWRITEBYTECODE=1 \
    # Flush Python output directly to container logs.
    PYTHONUNBUFFERED=1 \
    # Allow slow dependency downloads before timing out.
    UV_HTTP_TIMEOUT=300 \
    # Copy packages out of temporary BuildKit cache mounts.
    UV_LINK_MODE=copy \
    # Precompile installed packages, as in the original image.
    UV_COMPILE_BYTECODE=1 \
    # Keep the original dependency resolution policy explicit.
    UV_RESOLUTION=highest
COPY --from=uv_source /uv /uvx /bin/

# 3. NATIVE BUILD TOOLCHAIN (builder/dev only)
FROM os_base AS native_tools
RUN apt-get update && apt-get install \
    -y \
    --no-install-recommends \
    # C/C++ compiler, linker and make toolchain (includes gcc/g++).
    build-essential \
    # Trusted certificates for HTTPS downloads.
    ca-certificates \
    # Fetch the upstream GStreamer repository.
    git \
    # HTTPS downloads and network command-line utility.
    curl \
    # Download fallback dependency sources.
    wget \
    # Cache C/C++ compilation results between builds.
    ccache \
    # Generate lexical scanners.
    flex \
    # Backtrace/unwinding development files.
    libunwind-dev \
    # DWARF debug-information development files.
    libdw-dev \
    # Multiprecision arithmetic development files.
    libgmp-dev \
    # GLib/GObject development files.
    libglib2.0-dev \
    # Clang compiler and build tooling.
    clang \
    # Clang library headers, including support for Rust bindgen.
    libclang-dev \
    # Generate parsers.
    bison \
    # OpenSSL headers used by native dependencies and Rust crates.
    libssl-dev \
    # Desktop OpenGL development files.
    libgl1-mesa-dev \
    # EGL development files.
    libegl1-mesa-dev \
    # OpenGL ES development files.
    libgles2-mesa-dev \
    # Direct Rendering Manager development files.
    libdrm-dev \
    # Wayland client/server development files.
    libwayland-dev \
    # Protocol XML files used to generate the GstGL Wayland backend.
    wayland-protocols \
    # X11 development files.
    libx11-dev \
    # X11/XCB integration required to compile the GstGL X11 backend.
    libx11-xcb-dev \
    # Generic Buffer Management development files.
    libgbm-dev \
    # Legacy GObject introspection development tools and headers.
    libgirepository1.0-dev \
    # GIRepository development headers required by PyGObject.
    libgirepository-2.0-dev \
    # GIRepository introspection development metadata.
    gir1.2-girepository-3.0-dev \
    # Inspect GIRepository itself from Python, including in the SDK.
    gir1.2-girepository-3.0 \
    # Cairo headers required to build pycairo.
    libcairo2-dev \
    # Locate native libraries, compiler flags and headers.
    pkg-config && \
    rm -rf /var/lib/apt/lists/*
# Keep the build-tools interpreter fixed so selecting a runtime Python does not
# invalidate the native compilation. install-python.sh also registers libpython
# with ldconfig using its actual installation directory.
# Cargo stays on PATH for base too; its directory may be absent, which is harmless.
ENV \
    # Directory for uv-managed CPython installations.
    UV_PYTHON_INSTALL_DIR=/opt/build-python \
    # Active Python virtual environment.
    VIRTUAL_ENV=/opt/buildtools \
    # Prefer this environment and its build tools over system executables.
    PATH=/opt/buildtools/bin:/root/.cargo/bin:${PATH}
COPY build-utils/install-python.sh /opt/build-utils/install-python.sh
# Preserve the Docker build argument for selecting a Meson version.
ARG MESON_VERSION=1.12.1
# Meson configures, Ninja builds, CMake supports dependency fallbacks, and
# setuptools supplies Python build helpers. Jinja2/Pygments (documentation) and
# typogrify (website typography) are omitted because documentation is disabled.
RUN --mount=type=cache,id=gst-uv,target=/root/.cache/uv \
    bash /opt/build-utils/install-python.sh 3.12 /opt/buildtools && \
    uv pip install "meson==${MESON_VERSION}" ninja cmake setuptools
# minimal: GStreamer/Python; base: also GTK and gst-libav; full: also Rust.
ARG GSTREAMER_BUILD_PROFILE=base
RUN if [[ "$GSTREAMER_BUILD_PROFILE" != minimal ]]; then \
        apt-get update && apt-get install \
            -y \
            --no-install-recommends \
            # GTK4/Gdk/Gsk introspection for Python and stub generation.
            gir1.2-gtk-4.0 \
            # ATK introspection used by the stub generator's accessibility overrides.
            gir1.2-atk-1.0 && \
        rm -rf /var/lib/apt/lists/*; fi
RUN --mount=type=cache,id=gst-cargo-registry-26.04,target=/root/.cargo/registry \
    --mount=type=cache,id=gst-cargo-git-26.04,target=/root/.cargo/git \
    if [[ "$GSTREAMER_BUILD_PROFILE" == full ]]; then \
        curl \
            --proto '=https' \
            --tlsv1.2 \
            -sSf \
            https://sh.rustup.rs \
            | sh \
                -s \
                -- \
                -y \
                --profile minimal && \
        cargo install cargo-c \
            --locked; fi

# Optional GPL codecs: encoder tools/headers and the Intel non-free media driver.
# The native build script also explicitly enables/disables the Meson codec options.
ARG VARIANT=free
RUN if [[ "$VARIANT" == non-free ]]; then \
        apt-get update && apt-get install \
            -y \
            --no-install-recommends \
            # Intel non-free VA-API media driver.
            intel-media-va-driver-non-free \
            # H.264 encoder command-line tools (non-free option).
            x264 \
            # H.265 encoder command-line tools (non-free option).
            x265 \
            # H.264 encoder headers for the x264 GStreamer plugin.
            libx264-dev \
            # H.265 encoder headers for the x265 GStreamer plugin.
            libx265-dev && \
        rm -rf /var/lib/apt/lists/*; fi

# 4. GSTREAMER NATIVE BUILD (independent of the runtime Python)
# Release builds enable LTO; profile/non-free/build type remain separate dimensions.
FROM native_tools AS native_builder
ARG GSTREAMER_VERSION=1.28.8
ARG GSTREAMER_BUILD_TYPE=release
ARG BUILD_JOBS=6
ENV \
    # Upstream numeric GStreamer release.
    GSTREAMER_VERSION=${GSTREAMER_VERSION} \
    # Meson build type; release enables LTO.
    GSTREAMER_BUILD_TYPE=${GSTREAMER_BUILD_TYPE} \
    GSTREAMER_BUILD_PROFILE=${GSTREAMER_BUILD_PROFILE} \
    VARIANT=${VARIANT} \
    # Maximum Meson/Ninja compilation parallelism.
    BUILD_JOBS=${BUILD_JOBS} \
    # Maximum Cargo compilation parallelism.
    CARGO_BUILD_JOBS=${BUILD_JOBS} \
    # C/C++ compiler cache mount.
    CCACHE_DIR=/var/cache/ccache \
    # Cargo artifact cache mount.
    CARGO_TARGET_DIR=/var/cache/cargo-target
# Preserve the debugger in debug/debugoptimized builders; release stays unchanged.
RUN if [[ "$GSTREAMER_BUILD_TYPE" != release ]]; then \
        apt-get update && apt-get install \
            -y \
            --no-install-recommends \
            # Debugger for non-release development images.
            gdb && \
        rm -rf /var/lib/apt/lists/*; fi
# Fetch a numeric upstream release; scripts/build.sh checks tag existence first.
RUN [[ "$GSTREAMER_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && \
    git clone \
        --depth 1 \
        --branch "$GSTREAMER_VERSION" \
        https://gitlab.freedesktop.org/gstreamer/gstreamer.git /opt/gstreamer
# Shared Meson/Ninja logic: no docs/examples/tests; introspection stays enabled.
# DESTDIR=/install/gstreamer stages the native installation for the runtime COPY.
# Compiler/Cargo cache mounts are local to this builder, distro and architecture;
# registry/local layer-cache exports do not export these mount contents.
COPY build-utils/build-native.sh /opt/build-utils/build-native.sh
RUN --mount=type=cache,id=gst-ccache-26.04-amd64,target=/var/cache/ccache \
    --mount=type=cache,id=gst-cargo-target-26.04-amd64,target=/var/cache/cargo-target \
    --mount=type=cache,id=gst-cargo-registry-26.04,target=/root/.cargo/registry \
    --mount=type=cache,id=gst-cargo-git-26.04,target=/root/.cargo/git \
    bash /opt/build-utils/build-native.sh

# 5. TARGET PYTHON, BINDINGS AND WHEELS (also the published dev target)
# Retains compilers, source tree and native build directory for development.
FROM native_builder AS gstreamer_builder
# Source location, staged installation and release wheel output directory.
ENV \
    # Upstream source checkout.
    GSTREAMER_PATH=/opt/gstreamer \
    # Staged native installation copied into the runtime.
    GSTREAMER_INSTALL_DIR=/install/gstreamer \
    # Prebuilt wheel and build-metadata output.
    PY_WHEEL_DIR=/opt/wheel
# Install the requested runtime Python in /opt/uv/venv, separately from build tools.
# UV_TOOLCHAIN_DIR gives pkg-config a stable path across Python patch versions.
ARG PYTHON_VERSION=3.12
ENV \
    # Runtime CPython version selected by the build argument.
    PYTHON_VERSION=${PYTHON_VERSION} \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    VIRTUAL_ENV=/opt/uv/venv \
    # Use the target venv for uv project/run operations.
    UV_PROJECT_ENVIRONMENT=/opt/uv/venv \
    # Installation directory for uv tool executables.
    UV_TOOL_BIN_DIR=/opt/uv/bin \
    # Stable path used to locate the managed Python toolchain.
    UV_TOOLCHAIN_DIR=/opt/uv/toolchain-${PYTHON_VERSION} \
    PATH=/opt/uv/venv/bin:/opt/uv/bin:/opt/buildtools/bin:/root/.cargo/bin:${PATH}
RUN --mount=type=cache,id=gst-uv,target=/root/.cache/uv \
    bash /opt/build-utils/install-python.sh "$PYTHON_VERSION" "$VIRTUAL_ENV" && \
    ln -s "$(python -c 'import sys; print(sys.base_prefix)')" "$UV_TOOLCHAIN_DIR"
# Expose this interpreter and PyGObject headers to the standalone gst-python build.
ENV PKG_CONFIG_PATH=${VIRTUAL_ENV}/lib/pkgconfig:${UV_TOOLCHAIN_DIR}/lib/pkgconfig
# Build dependency wheels once, compile gst-python and its interpreter-specific
# libgstpython.so loader, then package the overrides/extensions with Hatchling.
# gst-python-binding avoids the gst-python package name; a separate packaging
# directory keeps generated wheel files out of the upstream source tree.
# Binding/metadata edits invalidate this step without recompiling native GStreamer.
RUN --mount=type=bind,src=build-utils,target=/tmp/build-utils \
    --mount=type=cache,id=gst-uv,target=/root/.cache/uv \
    bash /tmp/build-utils/build-python.sh
# Cache mounts are not exported; keep the native Cargo target symlink usable in SDKs.
RUN mkdir -p "$CARGO_TARGET_DIR"

# 6. RUNTIME IMAGE (default output; no compiler toolchain or source tree)
FROM os_base AS final
# Common GStreamer runtime dependencies; GTK/FFmpeg are added outside minimal.
ARG GSTREAMER_BUILD_PROFILE=base
ARG VARIANT=free
RUN apt-get update && apt-get install \
    -y \
    --no-install-recommends \
    curl \
    ca-certificates \
    # XZ archive utilities, including support for setup/downloads.
    xz-utils \
    # AC-3 audio decoding.
    liba52-0.7.4 \
    # Monochrome ASCII video output.
    libaa1 \
    # AV1 video codec runtime.
    libaom3 \
    # ALSA audio.
    libasound2t64 \
    # ASS/SSA subtitle rendering.
    libass9 \
    # Headphone stereo crossfeed audio filter.
    libbs2b0 \
    # Colour ASCII video output.
    libcaca0 \
    # Cairo/GObject integration.
    libcairo-gobject2 \
    # Cairo 2D rendering.
    libcairo2 \
    # Cairo script surfaces.
    libcairo-script-interpreter2 \
    # HTTP/TLS client library using GnuTLS.
    libcurl3t64-gnutls \
    # DTS audio decoding.
    libdca0 \
    # HEVC/H.265 decoding.
    libde265-0 \
    # Direct Rendering Manager runtime.
    libdrm2 \
    # DV video codec runtime.
    libdv4t64 \
    # DVD navigation.
    libdvdnav4 \
    # DVD reading.
    libdvdread8t64 \
    # EGL graphics API runtime.
    libegl1 \
    # GLX runtime selected by GstGL's automatic OpenGL platform detection.
    libglx0 \
    # Desktop OpenGL module loaded dynamically by GstGL.
    libgl1 \
    # OpenGL ES module loaded dynamically by GstGL.
    libgles2 \
    # AAC audio encoding.
    libfaac0 \
    # AAC audio decoding.
    libfaad2 \
    # FDK AAC audio codec runtime.
    libfdk-aac2 \
    # FLAC lossless audio.
    libflac14 \
    # Text-to-speech synthesis.
    libflite1 \
    # MIDI synthesis.
    libfluidsynth3 \
    # GTK image loading and pixel buffers.
    libgdk-pixbuf-2.0-0 \
    # Game music playback.
    libgme0 \
    # GSM speech codec.
    libgsm1 \
    # Device discovery through GUdev.
    libgudev-1.0-0 \
    # JPEG image codec.
    libjpeg-turbo8 \
    # Colour management.
    liblcms2-2 \
    # LV2 audio plugin hosting.
    liblilv-0-0 \
    # Intel oneVPL video API runtime; replaces libmfx1 on Ubuntu 26.04.
    libvpl2 \
    # mjpegtools support utilities.
    libmjpegutils-2.2-0 \
    # Tracker/module audio playback.
    libmodplug1 \
    # MP3 audio encoding.
    libmp3lame0 \
    # Musepack audio decoding.
    libmpcdec6 \
    # MPEG-2 video decoding.
    libmpeg2-4 \
    # MP3 audio decoding.
    libmpg123-0t64 \
    # Ogg container support.
    libogg0 \
    # AMR narrowband speech decoding.
    libopencore-amrnb0 \
    # AMR wideband speech decoding.
    libopencore-amrwb0 \
    # HDR image support.
    libopenexr-3-1-30 \
    # JPEG 2000 image codec.
    libopenjp2-7 \
    # Opus audio codec.
    libopus0 \
    # Text layout and shaping.
    libpango-1.0-0 \
    # Pango/Cairo text rendering.
    libpangocairo-1.0-0 \
    # PNG image codec.
    libpng16-16t64 \
    # PulseAudio integration.
    libpulse0 \
    # SVG rendering.
    librsvg2-2 \
    # Bluetooth SBC audio codec.
    libsbc1 \
    # Icecast streaming.
    libshout3 \
    # Audio file format I/O.
    libsndfile1 \
    # Audio tempo/pitch processing.
    libsoundtouch1 \
    # Telephony signal processing.
    libspandsp2t64 \
    # Speex speech codec.
    libspeex1 \
    # SRT network transport.
    libsrt1.5-gnutls \
    # Secure RTP transport.
    libsrtp2-1 \
    # Media file metadata.
    libtag2 \
    # Theora video codec runtime.
    libtheora1 \
    # Theora video decoder runtime.
    libtheoradec2 \
    # Theora video encoder runtime.
    libtheoraenc2 \
    # MP2 audio encoding.
    libtwolame0 \
    # VA-API video acceleration interface.
    libva2 \
    # Audio visualisation plugin framework.
    libvisual-0.4-0 \
    # VisualOn AAC encoding.
    libvo-aacenc0 \
    # AMR wideband speech encoding.
    libvo-amrwbenc0 \
    # Vorbis audio codec.
    libvorbis0a \
    # VP8/VP9 video codecs.
    libvpx12 \
    # WavPack lossless audio.
    libwavpack1 \
    # WebP image codec.
    libwebp7 \
    # WebP container demuxing.
    libwebpdemux2 \
    # MIDI playback.
    libwildmidi2 \
    # X11 client library.
    libx11-6 \
    # X11/XCB runtime used by the compiled GstGL X11 backend.
    libx11-xcb1 \
    # Barcode and QR code reading.
    libzbar0t64 \
    # Teletext/VBI support.
    libzvbi0t64 \
    # GLib/GObject runtime.
    libglib2.0-0t64 \
    # Native stack unwinding.
    libunwind8 \
    # DWARF debug-information runtime.
    libdw1t64 \
    # mjpegtools MPEG video encoding.
    libmpeg2encpp-2.2-0 \
    # XVideo display extension.
    libxv1 \
    # mjpegtools MPEG multiplexing.
    libmplex2-2.2-0 \
    # Legacy GObject introspection runtime.
    libgirepository-1.0-1 \
    # GIRepository runtime used by PyGObject.
    libgirepository-2.0-0 \
    gir1.2-girepository-3.0 \
    # GLib/GObject typelibs for Python introspection.
    gir1.2-glib-2.0 \
    # XTest extension, including screen capture support.
    libxtst6 \
    # Wayland display client runtime.
    libwayland-client0 \
    # Wayland cursor runtime used by the GstGL Wayland backend.
    libwayland-cursor0 \
    # Wayland/EGL integration used by the GstGL Wayland backend.
    libwayland-egl1 && \
    if [[ "$GSTREAMER_BUILD_PROFILE" != minimal ]]; then \
        apt-get install \
            -y \
            --no-install-recommends \
            # FFmpeg filter runtime.
            libavfilter11 \
            # Audio fingerprinting.
            libchromaprint1 \
            # GTK UI integration.
            libgtk-3-0t64 \
            gir1.2-gtk-4.0 \
            gir1.2-atk-1.0; fi && \
    if [[ "$VARIANT" == non-free ]]; then \
        apt-get install \
            -y \
            --no-install-recommends \
            # H.264 encoder runtime (non-free option).
            libx264-165 \
            # H.265 encoder runtime (non-free option).
            libx265-215 \
            intel-media-va-driver-non-free ; fi && \
    rm -rf /var/lib/apt/lists/*
# Copy the staged native installation before selecting Python, so both Python
# variants share this large runtime layer as well as the native compilation.
COPY --from=native_builder /install/gstreamer/ /
ARG PYTHON_VERSION=3.12
ENV \
    PYTHON_VERSION=${PYTHON_VERSION} \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    VIRTUAL_ENV=/opt/uv/venv \
    UV_PROJECT_ENVIRONMENT=/opt/uv/venv \
    UV_TOOL_BIN_DIR=/opt/uv/bin \
    UV_TOOLCHAIN_DIR=/opt/uv/toolchain-${PYTHON_VERSION} \
    PATH=/opt/uv/venv/bin:/opt/uv/bin:${PATH}
ENV PKG_CONFIG_PATH=${UV_TOOLCHAIN_DIR}/lib/pkgconfig
COPY build-utils/install-python.sh /opt/build-utils/install-python.sh
RUN --mount=type=cache,id=gst-uv,target=/root/.cache/uv \
    bash /opt/build-utils/install-python.sh "$PYTHON_VERSION" "$VIRTUAL_ENV" && \
    ln -s "$(python -c 'import sys; print(sys.base_prefix)')" "$UV_TOOLCHAIN_DIR"
# The plugin loader must match this Python; overrides/extensions come from wheels.
COPY --from=gstreamer_builder /install/python/usr/ /usr/
# Keep all dependency wheels and exact build metadata for offline venv installs
# and extraction by both release flows (not just gst_python_binding itself).
COPY --from=gstreamer_builder /opt/wheel/ /opt/wheel/
COPY --from=gstreamer_builder /opt/build-metadata/ /opt/build-metadata/
# Install the prebuilt wheels without network or a compiler. Bypass uv archive
# caching: wheels for different Ubuntu versions can have identical filenames.
RUN uv pip install \
        --no-cache \
        --no-index \
        --find-links=/opt/wheel \
        gst-python-binding && \
    ldconfig
