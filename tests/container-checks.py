"""Checks shared by the installed environment and fresh offline venvs."""
import importlib.metadata
import importlib.util
import ctypes
import hashlib
import json
from pathlib import Path
import sys
import sysconfig
import zipfile

environment = json.loads(Path('/opt/wheel/build-environment.json').read_text())
profile = environment['profile']
expected = environment['gstreamer_version']
binding_version = expected
if environment.get('release_revision', 1) != 1:
    binding_version += f'.post{environment["release_revision"]}'
if environment['build_type'] == 'debug':
    binding_version += '+debug'
assert importlib.metadata.version('gst-python-binding') == binding_version
has_analytics_extension = tuple(map(int, expected.split('.'))) >= (1, 28, 8)
import gi
for namespace in ('Gst', 'GstBase', 'GstVideo', 'GstApp', 'GstAudio', 'GstAnalytics', 'GES'):
    gi.require_version(namespace, '1.0')
for namespace, version in (
    ('GIRepository', '3.0'),
    ('GstGLX11', '1.0'),
    ('GstGLWayland', '1.0'),
):
    gi.require_version(namespace, version)
from gi.repository import Gst, GstBase, GstVideo, GstApp, GstAudio, GstAnalytics, GES
from gi.repository import GIRepository, GstGLX11, GstGLWayland
from gi.overrides import _gi_gst
if has_analytics_extension:
    from gi.overrides import _gi_gst_analytics

Gst.init(None)
# Imports and ELF resolution work headlessly; no display/GPU initialization is needed.
assert GIRepository.Repository.new().get_search_path()
if profile == 'minimal':
    assert not GIRepository.Repository.new().enumerate_versions('Gtk')
else:
    gi.require_version('Gtk', '4.0')
    gi.require_version('Atk', '1.0')
    from gi.repository import Gtk, Atk
    assert Gtk.get_major_version() == 4
    ctypes.CDLL('libgtk-4.so.1')
gst_gl = ctypes.CDLL('libgstgl-1.0.so.0')
for symbol in ('gst_gl_display_x11_new_with_display', 'gst_gl_display_wayland_new_with_display'):
    assert getattr(gst_gl, symbol), symbol
# GstGL loads these modules dynamically, so ldd alone cannot detect their absence.
for library in ('libGL.so.1', 'libGLESv2.so.2', 'libEGL.so.1'):
    ctypes.CDLL(library)
import cairo
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 2, 2)
assert surface.get_width() == 2
surface.finish()
# Exercise GStreamer -> PyGObject -> pycairo conversion of a real draw callback.
gi.require_foreign('cairo')
draw_calls = []


def draw_overlay(element, context, timestamp, duration):
    assert isinstance(context, cairo.Context), type(context)
    context.set_source_rgba(1, 0, 0, 1)
    context.rectangle(0, 0, 8, 8)
    context.fill()
    glyphs, clusters, flags = context.get_scaled_font().text_to_glyphs(0, 12, 'Gst')
    assert glyphs
    draw_calls.append(timestamp)


pipeline = Gst.parse_launch(
    'videotestsrc num-buffers=3 ! video/x-raw,format=BGRx,width=64,height=64 '
    '! cairooverlay name=overlay ! fakesink'
)
pipeline.get_by_name('overlay').connect('draw', draw_overlay)
try:
    assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
    message = pipeline.get_bus().timed_pop_filtered(
        10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
    )
    assert message is not None, 'Cairo overlay pipeline timed out'
    assert message.type == Gst.MessageType.EOS, message.parse_error()
    assert len(draw_calls) == 3, draw_calls
finally:
    pipeline.set_state(Gst.State.NULL)
assert Gst.version_string() == f'GStreamer {expected}', Gst.version_string()
assert str(Path(gi.__file__).resolve()).startswith(sys.prefix + '/'), gi.__file__
assert str(Path(_gi_gst.__file__).resolve()).startswith(sys.prefix + '/'), _gi_gst.__file__
assert str(Path(sys.modules['gi.overrides.GES'].__file__).resolve()).startswith(sys.prefix + '/')
assert Gst.Fraction(1, 2).num == 1
for path in Path('/opt/wheel').glob('*.whl'):
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.endswith('.so'):
                installed = Path(sysconfig.get_paths()['platlib']) / name
                assert hashlib.sha256(installed.read_bytes()).digest() == hashlib.sha256(archive.read(name)).digest(), (path.name, name, 'installed binary differs from its wheel')
for name in ('Gst', 'GstBase', 'GstVideo'):
    print('Namespace:', name)

for path in Path('/opt/wheel').glob('gst_python_binding-*.whl'):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        wheel = archive.read(next(n for n in names if n.endswith('/WHEEL'))).decode()
        assert 'Root-Is-Purelib: false' in wheel, wheel
        tag = f'cp{sys.version_info.major}{sys.version_info.minor}'
        assert f'Tag: {tag}-{tag}-linux_x86_64' in wheel, wheel
        assert any(n.startswith('gi/overrides/_gi_gst.') and n.endswith('.so') for n in names)
        if has_analytics_extension:
            assert any(n.startswith('gi/overrides/_gi_gst_analytics.') and n.endswith('.so') for n in names)
        assert 'gi/__init__.py' not in names and 'gi/overrides/__init__.py' not in names

spec = importlib.util.spec_from_file_location('square', '/audit/example_gst_plugin/py_rgb_square_with_property.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
for width, padded, bottom_up in ((320, False, False), (321, False, False), (321, True, False), (321, True, True)):
    height = 240
    caps = Gst.Caps.from_string(f'video/x-raw,format=RGB,width={width},height={height}')
    info = GstVideo.VideoInfo.new_from_caps(caps)
    stride = info.stride[0] + (16 if padded else 0)
    size = stride * height
    offset = stride * (height - 1) if bottom_up else 0
    if bottom_up:
        stride = -stride
    element = module.GstPyWithProperty()
    assert element.do_set_caps(caps, caps)
    inp = Gst.Buffer.new_allocate(None, size, None)
    inp.memset(0, 0, size)
    inp.pts, inp.dts, inp.duration = 1000, 900, 40
    if padded:
        GstVideo.buffer_add_video_meta_full(inp, GstVideo.VideoFrameFlags.NONE,
            GstVideo.VideoFormat.RGB, width, height, 1, [offset, 0, 0, 0], [stride, 0, 0, 0])
    flow, out = element.do_prepare_output_buffer(inp)
    assert flow == Gst.FlowReturn.OK
    assert (out.pts, out.dts, out.duration) == (1000, 900, 40)
    assert element.do_transform(inp, out) == Gst.FlowReturn.OK
    actual = out.extract_dup(0, size)
    expected_pixels = bytearray(size)
    x0, y0 = (width - 100) // 2, (height - 100) // 2
    for y in range(y0, y0 + 100):
        start = offset + y * stride + x0 * 3
        expected_pixels[start:start + 300] = bytes((255, 0, 0)) * 100
    assert actual == expected_pixels, (width, padded, bottom_up)
short = Gst.Buffer.new_allocate(None, 16, None)
element = module.GstPyWithProperty()
flow, out = element.do_prepare_output_buffer(short)
assert flow == Gst.FlowReturn.OK
assert element.do_transform(short, out) == Gst.FlowReturn.NOT_NEGOTIATED
assert element.do_set_caps(Gst.Caps.from_string('video/x-raw,format=RGB,width=321,height=240'),
                           Gst.Caps.from_string('video/x-raw,format=RGB,width=321,height=240'))
assert element.do_transform(short, out) == Gst.FlowReturn.ERROR
print('Python, wheel, RGB stride/metadata/timestamp checks passed:', sys.version, sys.prefix)
