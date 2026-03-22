from __future__ import annotations

import gi

gi.require_version("GObject", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstBase", "1.0")

from gi.repository import Gst, GstBase, GObject


class GstPyWithPropertyIP(GstBase.BaseTransform):
    GST_PLUGIN_NAME = "py-with-property"
    __gtype_name__ = GST_PLUGIN_NAME

    __gstmetadata__ = (
        f"{GST_PLUGIN_NAME}",
        "Video/Identity/Test",
        f"{GST_PLUGIN_NAME} is a test plugin in Python with a custom property.",
        "Author Name",
    )

    CAPS = Gst.Caps.new_any()

    __gsttemplates__ = (
        Gst.PadTemplate.new(
            "src",
            Gst.PadDirection.SRC,
            Gst.PadPresence.ALWAYS,
            CAPS,
        ),
        Gst.PadTemplate.new(
            "sink",
            Gst.PadDirection.SINK,
            Gst.PadPresence.ALWAYS,
            CAPS,
        ),
    )

    _test: str = "not-initialized"
    def set_test(self, value: str) -> None:
        print(f"Setting test property to: {value}")
        self._test = value

    @GObject.Property(
        type=GObject.TYPE_STRING,
        nick="test",
        blurb="test string property",
        default="",
        flags=GObject.ParamFlags.READWRITE,
        setter=set_test,
    )
    def test(self) -> str:
        return self._test

    # this make stubs unhappy
    # @test.setter
    # def test(self, value) -> None:
    #     print(f"in setter: {value}")

    def __init__(self):
        super().__init__()
        self.test = "initialize-in-init"

    def do_transform_ip(self, buf: Gst.Buffer) -> Gst.FlowReturn:
        print("FRAME")
        return Gst.FlowReturn.OK

    def do_set_caps(self, incaps: Gst.Caps, outcaps: Gst.Caps) -> bool:
        self.set_passthrough(True)
        return True


__gstelementfactory__ = (GstPyWithPropertyIP.GST_PLUGIN_NAME, Gst.Rank.NONE, GstPyWithPropertyIP)
