from __future__ import annotations

import gi

gi.require_version("GObject", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("GstBase", "1.0")
gi.require_version("GLib", "2.0")

from gi.repository import Gst, GstBase, GObject, GLib


class GstPyWithProperty(GstBase.BaseTransform):
    GST_PLUGIN_NAME = "py-rgb-square-with-property"
    __gtype_name__ = GST_PLUGIN_NAME

    __gstmetadata__ = (
        f"{GST_PLUGIN_NAME}",
        "Video/Identity/Test",
        f"{GST_PLUGIN_NAME} is a test plugin in Python that processes video not in place. It adds a custom property and draws a red square in the center of the video frame.",
        "Author Name",
    )

    CAPS = Gst.Caps.from_string("video/x-raw,format=RGB")

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

    _size: int = 100
    def set_size(self, value: int) -> None:
        print(f"Setting size property to: {value}")
        self._size = value

    @GObject.Property(
        type=GObject.TYPE_INT,
        nick="size",
        blurb="size of the red square",
        default=100,
        flags=GObject.ParamFlags.READWRITE,
        setter=set_size,
    )
    def size(self) -> int:
        return self._size

    def __init__(self):
        super().__init__()
        self.size = 100
        self.buffer_count = 0

    def do_prepare_output_buffer(self, input: Gst.Buffer):
        """
        Allocates a new output buffer with its own memory and copies metadata from input.
        """
        new_outbuffer = Gst.Buffer.new_allocate(None, input.get_size(), None)

        METADATA_FLAGS = (
            Gst.BufferCopyFlags.FLAGS
            | Gst.BufferCopyFlags.TIMESTAMPS
            | Gst.BufferCopyFlags.META
        )

        if not new_outbuffer.copy_into(input, METADATA_FLAGS, 0, GLib.MAXSIZE):
            print("Failed to copy metadata")
            return Gst.FlowReturn.ERROR, new_outbuffer

        return Gst.FlowReturn.OK, new_outbuffer
    

    def do_transform(self, inbuf, outbuf):
        self.buffer_count += 1
        print(f"do_transform: processing buffer #{self.buffer_count}")
        success_in, map_in = inbuf.map(Gst.MapFlags.READ)
        success_out, map_out = outbuf.map(Gst.MapFlags.WRITE)

        # if mapping fails, we need to unmap the successfully mapped buffer before returning
        if not success_in or not success_out:
            if success_in: 
                inbuf.unmap(map_in)
            if success_out: 
                outbuf.unmap(map_out)
            return Gst.FlowReturn.ERROR

        try:
            data_in = map_in.data
            data_out = map_out.data
            
            # Quick example with memoryview or slicing
            data_out[:] = data_in
            
            # Draw a 100x100 red square in the center (assuming RGB, tightly packed)
            width = 320  # default test width
            height = 240 # default test height
            pixel_size = 3  # RGB
            square_size = self.size
            # Try to get width/height from caps if possible
            try:
                caps = self.srcpad.get_current_caps()
                s = caps.get_structure(0)
                width = s.get_int('width')[1] or width
                height = s.get_int('height')[1] or height
            except Exception:
                print("Could not get width/height from caps, using defaults")
                pass
            x0 = max(0, (width - square_size) // 2)
            y0 = max(0, (height - square_size) // 2)
            for y in range(y0, min(y0 + square_size, height)):
                for x in range(x0, min(x0 + square_size, width)):
                    idx = (y * width + x) * pixel_size
                    if idx + 2 < len(data_out):
                        data_out[idx] = 255   # R
                        data_out[idx+1] = 0   # G
                        data_out[idx+2] = 0   # B
            
            # If everything is ok, return OK
            return Gst.FlowReturn.OK

        except Exception as e:
            print(f"Error: {e}")
            return Gst.FlowReturn.ERROR

        finally:
            inbuf.unmap(map_in)
            outbuf.unmap(map_out)
    

    def do_set_caps(self, incaps: Gst.Caps, outcaps: Gst.Caps) -> bool:
        self.set_passthrough(False)
        return True


__gstelementfactory__ = (GstPyWithProperty.GST_PLUGIN_NAME, Gst.Rank.NONE, GstPyWithProperty)
