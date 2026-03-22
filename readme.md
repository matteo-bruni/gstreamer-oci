# GStreamer OCI Images

A collection of build definitions to create container images with GStreamer compiled from source. 
The image is a minimal Ubuntu image with GStreamer with the minimal set of plugins to be able to build gst-python.

## Usage

List available recipes:

```
just
```

### Build

```
just build-debug
just build-release
```

### Run example plugins

Once the build-debug is complete, you can enter the container with:
```
just run-example
```

once inside the container you should see the plugins doing:

```
gst-inspect-1.0 python
```

There are two plugins included in the `example_gst_plugin` directory:
- `py-with-property`: simple plugin with a string property
- `py-rgb-square-with-property`: plugin that draw a red square of a given size on top of the input buffer. (size can be set as a property). this plugin create its output buffer (doest not work `in-place`).

to test the plugin is working:

```
gst-launch-1.0 \
    videotestsrc \
    ! py-with-property \
    ! fakevideosink
```

```
gst-launch-1.0 \
    videotestsrc \
    ! video/x-raw,format=RGB,width=320,height=240 \
    ! py-rgb-square-with-property \
    ! fakevideosink
```

by default videotestsrc will generate infinite output, to limit it to a certain number of frames, set num-buffers

```  
videotestsrc num-buffers=10
```


### Default parameters

The recipes `build-debug`, `build-release` and `run-example` accept positional parameters with defaults:

| Parameter           | Default   |
|---------------------|-----------|
| `BASE_IMAGE`        | `ubuntu`  |
| `BASE_TAG`          | `24.04`   |
| `GSTREAMER_VERSION` | `1.28.1`  |
| `PYTHON_VERSION`    | `3.12`    |
| `UV_VERSION`        | `latest`  |

Override any parameter positionally:

```
just build-debug ubuntu 25.10 1.26.2
just build-release ubuntu 24.04 1.28.1 3.13 latest
```

### Custom build type

The `build` recipe requires all parameters explicitly (no defaults):

```
just build debug ubuntu 24.04 1.28.1 3.12 latest
just build debugoptimized ubuntu 25.10 1.26.2 3.13 latest
```

