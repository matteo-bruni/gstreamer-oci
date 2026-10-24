#!/usr/bin/env bash
set -euo pipefail
image="$1"
root="$(cd "$(dirname "$0")/.." && pwd)"
# The same check runs without network and without access to host site-packages.
docker run \
    --rm \
    --network=none \
    -v "$root:/audit:ro" \
    "$image" bash \
        -euo pipefail \
        -c '
    export GST_REGISTRY=/tmp/gst-registry.bin GST_PLUGIN_PATH=/tmp/gst
    mkdir -p /tmp/gst
    ln -s /audit/example_gst_plugin /tmp/gst/python
    python /audit/tests/container-checks.py
    gst-inspect-1.0 python >/dev/null
    gst-launch-1.0 -q \
        videotestsrc num-buffers=10 \
        ! py-with-property \
        ! fakevideosink
    gst-launch-1.0 -q \
        videotestsrc num-buffers=2 \
        ! video/x-raw,format=RGB,width=321,height=240 \
        ! py-rgb-square-with-property \
        ! fakevideosink
    python - <<'"'"'PY'"'"'
import json, subprocess
from pathlib import Path
meta = json.loads(Path("/opt/wheel/build-environment.json").read_text())
options = {item["name"]: item["value"] for item in json.loads(Path("/opt/build-metadata/meson-options.json").read_text())}
assert options["buildtype"] == meta["build_type"]
assert options["examples"] == "disabled"
assert options["libav"] == ("disabled" if meta["profile"] == "minimal" else "enabled")
assert options["rs"] == ("enabled" if meta["profile"] == "full" else "disabled")
if meta["build_type"] == "release":
    assert options["b_lto"] is True
driver = subprocess.run([
    "dpkg-query",
    "-W",
    "-f=${Status}",
    "intel-media-va-driver-non-free",
], text=True, capture_output=True)
assert (driver.returncode == 0 and driver.stdout == "install ok installed") == (meta["variant"] == "non-free")
if meta["variant"] == "non-free":
    for enc in ("x264enc", "x265enc"):
        subprocess.run([
            "gst-launch-1.0", "-q",
            "videotestsrc", "num-buffers=2",
            "!", "video/x-raw,format=I420,width=64,height=64",
            "!", enc,
            "!", "fakesink",
        ], check=True)
else:
    for enc in ("x264enc", "x265enc"):
        assert subprocess.run(["gst-inspect-1.0", enc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
if meta["profile"] != "minimal":
    subprocess.run(["gst-inspect-1.0", "avdec_h264"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([
        "gst-launch-1.0", "-q",
        "videotestsrc", "num-buffers=2",
        "!", "video/x-raw,format=I420,width=64,height=64",
        "!", "avenc_ffv1",
        "!", "avdec_ffv1",
        "!", "fakesink",
    ], check=True)
if meta["profile"] == "full":
    subprocess.run([
        "gst-launch-1.0", "-q",
        "videotestsrc", "num-buffers=2",
        "!", "video/x-raw,format=RGB",
        "!", "hsvfilter",
        "!", "fakesink",
    ], check=True)
else:
    assert subprocess.run(["gst-inspect-1.0", "hsvfilter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
if meta["profile"] == "minimal":
    packages = subprocess.check_output(["dpkg-query", "-W", "-f=${binary:Package} ${db:Status-Status}\n"], text=True)
    assert not any(line.startswith(("libgtk-3-", "libgtk-4-", "gir1.2-gtk-")) and line.endswith(" installed")
                   for line in packages.splitlines()), "GTK installed in minimal"
    assert subprocess.run(["gst-inspect-1.0", "avdec_h264"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
# This validation plugin deliberately declines loading until GstValidate is initialized.
blacklist = subprocess.check_output(["gst-inspect-1.0", "-b"], text=True)
entries = {line.strip() for line in blacklist.splitlines() if line.startswith("  ")}
assert entries <= {"libgstvalidatessim.so"}, blacklist
# ldd reports missing dependencies while often still exiting successfully.
native_paths = {line.split("  ", 1)[1].replace("/install/gstreamer", "", 1)
                for line in Path("/opt/build-metadata/native-manifest.sha256").read_text().splitlines()}
for p in list(Path("/usr/lib").rglob("libgst*.so*")) + list(Path("/opt/uv/venv").rglob("*.so")):
    result = subprocess.run(["ldd", str(p)], text=True, capture_output=True)
    assert "not found" not in result.stdout, (str(p), result.stdout)
    if str(p) in native_paths:
        assert "libpython" not in result.stdout, (str(p), "native stage links a target Python", result.stdout)
PY
    uv venv --python "$VIRTUAL_ENV/bin/python" /tmp/root-venv
    uv pip install \
        --no-cache \
        --python /tmp/root-venv/bin/python \
        --no-index \
        --find-links=/opt/wheel \
        gst-python-binding
    /tmp/root-venv/bin/python /audit/tests/container-checks.py
'
docker run \
    --rm \
    --network=none \
    --user 10001:10001 \
    -e HOME=/tmp \
    -e UV_NO_CACHE=1 \
    -v "$root:/audit:ro" \
    "$image" bash \
        -euo pipefail \
        -c '
    uv venv --python "$VIRTUAL_ENV/bin/python" /tmp/user-venv
    uv pip install \
        --no-cache \
        --python /tmp/user-venv/bin/python \
        --no-index \
        --find-links=/opt/wheel \
        gst-python-binding
    /tmp/user-venv/bin/python /audit/tests/container-checks.py
'
