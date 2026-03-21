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

### Run example plugin

```
just run-example
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

