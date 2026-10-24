#!/usr/bin/env bash
set -euo pipefail
[[ "$GSTREAMER_BUILD_PROFILE" =~ ^(minimal|base|full)$ && "$VARIANT" =~ ^(free|non-free)$ ]] || {
    echo 'Invalid build profile or variant' >&2
    exit 1
}
cd /opt/gstreamer

options=(
    --prefix=/usr
    --buildtype="$GSTREAMER_BUILD_TYPE"
    --warnlevel=0
    -Dbase=enabled
    -Dgood=enabled
    -Dbad=enabled
    -Dugly=enabled
    -Drtsp_server=enabled
    -Dintrospection=enabled
    # GTK links these GstGL backends; fail instead of silently omitting them.
    -Dgst-plugins-base:gl=enabled
    -Dgst-plugins-base:gl_winsys=x11,wayland,egl,surfaceless
    -Dpython=disabled
    -Ddoc=disabled
    -Dtests=disabled
    -Dexamples=disabled
    -Dgst-examples=disabled
    -Dpango:build-testsuite=false
    -Dpango:build-examples=false
    -Dpackage-origin=https://gitlab.freedesktop.org/gstreamer/gstreamer.git
)
if [[ "$GSTREAMER_BUILD_TYPE" == release ]]; then
    options+=(
        -Db_lto=true
        "-Db_lto_threads=$BUILD_JOBS"
    )
fi
if [[ "$GSTREAMER_BUILD_PROFILE" == minimal ]]; then
    options+=( -Dlibav=disabled )
else
    options+=( -Dlibav=enabled )
fi
if [[ "$GSTREAMER_BUILD_PROFILE" == full ]]; then
    options+=(
        -Drs=enabled
        -Dgst-plugins-rs:csound=disabled
    )
else
    options+=(
        -Drs=disabled
    )
fi
if [[ "$VARIANT" == non-free ]]; then
    options+=(
        -Dgpl=enabled
        -Dgst-plugins-ugly:x264=enabled
        -Dgst-plugins-bad:x265=enabled
    )
else
    options+=(
        -Dgpl=disabled
        -Dgst-plugins-ugly:x264=disabled
        -Dgst-plugins-bad:x265=disabled
    )
fi

# Bash's elapsed-seconds counter records the duration of this native build phase.
start=$SECONDS
meson setup build "${options[@]}" 2>&1 | tee build.log
if [[ "$GSTREAMER_BUILD_PROFILE" == full ]]; then
    # Upstream passes --target-dir explicitly, overriding CARGO_TARGET_DIR.
    ln -s "$CARGO_TARGET_DIR" build/subprojects/gst-plugins-rs/target
fi
meson compile \
    -C build \
    -j "$BUILD_JOBS" \
    2>&1 | tee -a build.log
DESTDIR=/install/gstreamer meson install \
    -C build \
    --no-rebuild \
    2>&1 | tee -a build.log
cp -a /install/gstreamer/. /
ldconfig
# Keep build provenance and verification data alongside the installed artifacts.
mkdir -p /opt/build-metadata
# Record elapsed native setup/build/install time; cached stages retain this value.
printf '%s\n' "$((SECONDS-start))" > /opt/build-metadata/native-seconds
# Save resolved Meson options so checks can confirm the profile, LTO and features.
meson introspect build --buildoptions > /opt/build-metadata/meson-options.json
# Identify the exact upstream source commit, independently of the release tag.
git rev-parse HEAD > /opt/build-metadata/gstreamer-commit
# Record installed Python build-tool versions, including Meson, Ninja and CMake.
uv pip freeze > /opt/build-metadata/build-requirements.txt
# Record the Rust compiler version when the full profile installed its toolchain.
if command -v rustc >/dev/null; then rustc --version > /opt/build-metadata/rust-toolchain.txt; fi
# Hash staged native libraries and typelibs to compare artifacts across Python variants.
# NUL-delimited paths handle whitespace safely; sorting keeps the manifest ordered.
find /install/gstreamer/usr \
    -type f \
    \( \
        -name '*.so*' \
        -o \
        -name '*.typelib' \
    \) \
    -print0 \
    | sort -z \
    | xargs -0 sha256sum > /opt/build-metadata/native-manifest.sha256
