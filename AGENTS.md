# AGENTS.md

## Repository purpose

This repository builds OCI images containing GStreamer compiled from source and
a redistributable `gst_python_binding` wheel. It is primarily Docker, Bash, and
Just orchestration; there is no application service or local unit-test suite.

## Start here

- `readme.md`: supported build variants and user-facing commands.
- `justfile`: local command interface and image-tag construction.
- `Dockerfile`: source of truth for image contents and the build pipeline.
- `build-utils/gst-python-binding/pyproject.toml`: wheel metadata template.
- `example_gst_plugin/`: Python plugins used for container smoke tests.
- `scripts/local-release.sh`: interactive local release flow.
- `.github/workflows/manual-release.yml`: equivalent manual GitHub release flow.

Prefer the implementation over prose when they disagree. Before editing, run
`git status --short` and preserve all existing user changes.

## How the build works

`justfile` passes positional options to Docker and generates the local image
tag. The Dockerfile then builds these stages:

1. `uv_source` supplies the `uv` binaries.
2. `base_with_uv` installs the requested Python and creates `/opt/uv/venv`.
3. `gstreamer_builder` clones the requested GStreamer revision, configures it
   with Meson, builds it with Ninja, stages the installation, and creates the
   GStreamer Python wheel plus its binary dependencies under `/opt/wheel`.
4. `final` installs runtime packages, copies the staged GStreamer installation,
   installs the wheels, and retains `gst_python_binding` in `/opt/wheel` for
   release tooling.

The main build dimensions are independent:

- `GSTREAMER_BUILD_PROFILE`: `base`, or `full` with gst-libav and Rust plugins.
- `GSTREAMER_BUILD_TYPE`: `release`, `debug`, or `debugoptimized`.
- `GSTREAMER_DEV`: `false` selects `final`; `true` publishes
  `gstreamer_builder` with the toolchain and source tree.
- `GSTREAMER_ENABLE_NON_FREE`: requests non-free codecs and drivers.
- `PYTHON_VERSION`, `GSTREAMER_VERSION`, base image, and `UV_VERSION` select the
  toolchain and upstream inputs.

The pyproject version `0.0.0` is intentional: the Docker build copies the file
as a template and replaces the version with the GStreamer version. Debug wheels
receive the `+debug` local suffix.

Current caveat: `GSTREAMER_ENABLE_NON_FREE` is not wired consistently. The
builder defines it as a fixed `ENV`, while the final stage builds
`EXTRA_APT_PKGS` but installs `$APT_PKGS`. Do not assume the switch changes the
image contents; trace and fix both stages when working on this option.

## Fast command reference

```bash
just
just build-debug
just build-debugoptimized
just build-release
```

The preset recipes use defaults shown by `just --list`; all options are
positional. Use the generic recipe only when every value must be explicit:

```bash
just build <build-type> <base-image> <base-tag> <gstreamer-version> \
  <profile> <non-free> <dev> <python-version> <uv-version>
```

Compute a tag without building:

```bash
just image-tag release ubuntu 24.04 1.28.3 base false false 3.12
```

After building, open a container with the example plugins mounted read-only:

```bash
just run-example ubuntu 24.04 1.28.3 base false false 3.12 debug
```

Use exactly the same build options. Inside the container, smoke-test with:

```bash
gst-inspect-1.0 python
gst-launch-1.0 videotestsrc num-buffers=10 ! py-with-property ! fakevideosink
```

Builds are network-, CPU-, and time-intensive. Buildx debug may open an
interactive shell on failure; if its terminal size is broken, run
`stty rows 75 cols 254` followed by `reset`.

## Change and verification rules

- Keep changes surgical and update `readme.md` for user-visible behavior.
- Build options, defaults, tags, release notes, and metadata are duplicated
  across `justfile`, `Dockerfile`, `readme.md`, the local release script, and
  the GitHub workflow. Grep all consumers and keep both release paths aligned.
- Trace build settings end to end: Just argument, Docker build argument,
  Docker `ARG`/`ENV`, image tag, release tag, notes, and JSON metadata.
- `release-assets/` is ignored generated output; do not treat it as source.
- There is no unit-test command. Use the smallest relevant verification:
  `just --list`, representative `just image-tag` calls,
  `bash -n scripts/local-release.sh`, and `git diff --check`.
- For Docker or plugin changes, run the narrowest relevant image build, then
  `run-example` and the smoke checks above. Do not run every build variant
  unless the change affects their matrix.

## Release safety

Do not run `scripts/local-release.sh` or trigger the manual workflow without
explicit user authorization. The local script requires authenticated `gh`,
recreates ignored `release-assets/` even in dry-run, and always performs full
Docker builds. Without dry-run it also logs in to GHCR, pushes two image tags
per Python version, and creates a GitHub Release.
