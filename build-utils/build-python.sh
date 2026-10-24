#!/usr/bin/env bash
set -euo pipefail

PYGOBJECT_VERSION='3.58.1'
PYCAIRO_VERSION='1.29.2'
TYPING_EXTENSIONS_VERSION='4.15.0'

# Measure this phase, including dependency wheels, bindings and packaging.
start=$SECONDS
mkdir -p /opt/wheel
# Match the requested build type for dependency wheels; release also enables LTO.
wheel_options=(
    "--config-settings=setup-args=-Dbuildtype=$GSTREAMER_BUILD_TYPE"
)
if [[ "$GSTREAMER_BUILD_TYPE" == release ]]; then
    wheel_options+=(
        --config-settings=setup-args=-Db_lto=true
        "--config-settings=setup-args=-Db_lto_threads=$BUILD_JOBS"
    )
fi
# Build pycairo first so PyGObject can reuse this wheel in its isolated build.
pycairo_requirement="pycairo==$PYCAIRO_VERSION"
mkdir -p /opt/build-metadata
printf '%s\n' "$pycairo_requirement" > /opt/build-metadata/python-build-constraints.txt
uv run \
    --no-project \
    --with pip \
    pip wheel \
        --wheel-dir /opt/wheel \
        --no-binary pycairo \
        "${wheel_options[@]}" \
        "$pycairo_requirement" \
        "typing-extensions==$TYPING_EXTENSIONS_VERSION"
# Constrain the isolated build too, so it cannot select another pycairo version.
uv run \
    --no-project \
    --with pip \
    pip wheel \
        --wheel-dir /opt/wheel \
        --find-links /opt/wheel \
        --build-constraint /opt/build-metadata/python-build-constraints.txt \
        --no-binary PyGObject \
        "${wheel_options[@]}" \
        "PyGObject==$PYGOBJECT_VERSION" \
        "$pycairo_requirement"
# Local wheels have identical filenames across Ubuntu versions; bypass uv's archive cache.
uv pip install \
    --no-cache \
    --no-index \
    --find-links /opt/wheel \
    PyGObject pycairo typing-extensions

# PyGObject wheels ship their C header but omit the pkg-config descriptor.
python - <<'PY'
import importlib.metadata
from pathlib import Path
import sys
headers = list((Path(sys.prefix) / 'include').rglob('pygobject.h'))
assert len(headers) == 1, headers
pc = Path(sys.prefix) / 'lib/pkgconfig/pygobject-3.0.pc'
pc.parent.mkdir(parents=True, exist_ok=True)
pc.write_text(f'Name: PyGObject\nDescription: PyGObject wheel headers\nVersion: {importlib.metadata.version("PyGObject")}\nRequires: gobject-2.0\nCflags: -I{headers[0].parent}\n')
PY
export PKG_CONFIG_PATH="$VIRTUAL_ENV/lib/pkgconfig:$PKG_CONFIG_PATH"

python_exe="$VIRTUAL_ENV/bin/python"
options=(
    --prefix=/usr
    --buildtype="$GSTREAMER_BUILD_TYPE"
    -Dtests=disabled
    "-Dpython-exe=$python_exe"
    "-Dpygi-overrides-dir=$VIRTUAL_ENV/lib/python$PYTHON_VERSION/site-packages/gi/overrides"
)
if [[ "$GSTREAMER_BUILD_TYPE" == release ]]; then
    options+=(
        -Db_lto=true
        "-Db_lto_threads=$BUILD_JOBS"
    )
fi
meson setup /opt/gst-python-build /opt/gstreamer/subprojects/gst-python "${options[@]}"
meson compile \
    -C /opt/gst-python-build \
    -j "$BUILD_JOBS"
DESTDIR=/install/python meson install \
    -C /opt/gst-python-build \
    --no-rebuild
# Only the loader belongs in /usr; the overrides are installed from the wheel.
rm -rf /install/python/usr/lib/python*
cp -a /install/python/usr/. /usr/
cp -a /install/python/usr/. /install/gstreamer/usr/

package=/opt/gst-python-binding
mkdir -p "$package/src/gi/overrides"
cp /tmp/build-utils/gst-python-binding/{pyproject.toml,hatch_build.py} "$package/"
cp /opt/gstreamer/subprojects/gst-python/COPYING "$package/"
version="$GSTREAMER_VERSION"
if [[ "$GSTREAMER_BUILD_TYPE" == debug ]]; then version+='+debug'; fi
sed -i "s/version = \"0.0.0\"/version = \"$version\"/" "$package/pyproject.toml"
cp /opt/gst-python-build/gi/overrides/_gi_gst*.so "$package/src/gi/overrides/"
cp /opt/gstreamer/subprojects/gst-python/gi/overrides/Gst*.py "$package/src/gi/overrides/"
# GES uses the parent python option, so install its pure override for each target here.
if [[ -f /usr/lib/x86_64-linux-gnu/girepository-1.0/GES-1.0.typelib ]]; then
    cp /opt/gstreamer/subprojects/gst-editing-services/bindings/python/gi/overrides/GES.py "$package/src/gi/overrides/"
fi
uv build \
    --python "$python_exe" \
    --wheel \
    --out-dir /opt/wheel \
    "$package"
uv pip install \
    --no-cache \
    --no-index \
    --find-links /opt/wheel \
    gst-python-binding
# Do not retain a registry generated before the Python plugin loader was installed.
rm -f /root/.cache/gstreamer-1.0/registry.x86_64.bin
ldconfig
uv pip freeze > /opt/build-metadata/python-requirements.txt
printf '%s\n' "$((SECONDS-start))" > /opt/build-metadata/python-seconds
python /tmp/build-utils/wheel-metadata.py > /opt/wheel/build-environment.json
