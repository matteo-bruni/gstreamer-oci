import ctypes
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import subprocess


def native_environment():
    import cairo

    glib = ctypes.CDLL('libglib-2.0.so.0')
    glib_version = '.'.join(str(ctypes.c_uint.in_dll(glib, f'glib_{part}_version').value)
                            for part in ('major', 'minor', 'micro'))
    dependencies = {
        'glib': {'runtime': glib_version},
        'gobject': {},
        'cairo': {'runtime': cairo.cairo_version_string()},
        'glibc': {'runtime': platform.libc_ver()[1]},
    }
    for name, package in (('glib', 'glib-2.0'), ('gobject', 'gobject-2.0'), ('cairo', 'cairo')):
        dependencies[name]['build'] = subprocess.check_output(
            ['pkg-config', '--modversion', package], text=True).strip()
    # Read the compiler and Meson actually selected by this binding build.
    info = Path('/opt/gst-python-build/meson-info')
    compiler = json.loads((info / 'intro-compilers.json').read_text())['host']['c']
    meson = json.loads((info / 'meson-info.json').read_text())['meson_version']['full']
    return {
        'native_dependencies': dependencies,
        'build_tools': {
            'c_compiler': {key: compiler[key] for key in ('id', 'version', 'full_version', 'exelist')},
            'meson': meson,
        },
    }


def build_environment():
    return {
        "gstreamer_version": os.environ["GSTREAMER_VERSION"],
        "gstreamer_commit": Path("/opt/build-metadata/gstreamer-commit").read_text().strip(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "ubuntu": Path("/etc/os-release").read_text(),
        "profile": os.environ["GSTREAMER_BUILD_PROFILE"],
        "variant": os.environ["VARIANT"],
        "build_type": os.environ["GSTREAMER_BUILD_TYPE"],
        "release_revision": int(os.environ.get("RELEASE_REVISION", "1")),
        "requirements": {name: importlib.metadata.version(name) for name in
                         ("gst-python-binding", "PyGObject", "pycairo", "typing-extensions")},
    } | native_environment()


if __name__ == '__main__':
    print(json.dumps(build_environment(), indent=2))
