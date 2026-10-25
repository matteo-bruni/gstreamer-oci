import importlib.metadata
import json
import os
import platform
from pathlib import Path

print(json.dumps({
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
}, indent=2))
