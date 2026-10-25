#!/usr/bin/env python3
"""Build a static Simple API index from verified, public GitHub release assets."""

import argparse
from email.parser import BytesParser
import hashlib
import html
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile


PROJECTS = ("gst-python-binding", "pygobject", "pycairo", "typing-extensions")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fetch(url):
    request = Request(url, headers={"User-Agent": "gstreamer-oci-wheel-index"})
    with urlopen(request, timeout=60) as response:
        data = response.read(32 * 1024 * 1024 + 1)
    require(len(data) <= 32 * 1024 * 1024, f"Asset exceeds 32 MiB: {url}")
    return data


def github_releases(repository):
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"],
        check=True, capture_output=True, text=True,
    )
    return [release for page in json.loads(result.stdout) for release in page]


def wheel_info(filename, data):
    parts = filename.removesuffix(".whl").split("-")
    require(filename.endswith(".whl") and len(parts) == 5, f"Unexpected wheel: {filename}")
    name, version, python, abi, platform = parts
    project = normalize(name)
    require(project in PROJECTS, f"Unexpected project: {filename}")
    if project == "typing-extensions":
        require((python, abi, platform) == ("py3", "none", "any"), f"Not universal: {filename}")
    else:
        require(python in ("cp312", "cp314") and abi == python and platform == "linux_x86_64",
                f"Unsupported ABI/platform: {filename}")
    with zipfile.ZipFile(io.BytesIO(data)) as wheel:
        names = wheel.namelist()
        require(len(names) == len(set(names)), f"Duplicate wheel entries: {filename}")
        # Compare installed files and permissions, independent of ZIP timestamps/compression.
        contents = [(item.filename, item.external_attr, hashlib.sha256(wheel.read(item)).hexdigest())
                    for item in sorted(wheel.infolist(), key=lambda item: item.filename)]
        metadata_files = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        require(len(metadata_files) == 1, f"Expected one METADATA: {filename}")
        directory = metadata_files[0].removesuffix("METADATA")
        metadata = BytesParser().parsebytes(wheel.read(directory + "METADATA"))
        wheel_metadata = BytesParser().parsebytes(wheel.read(directory + "WHEEL"))
        require(normalize(metadata["Name"]) == project and metadata["Version"] == version,
                f"Wheel filename/METADATA mismatch: {filename}")
        require(f"{python}-{abi}-{platform}" in wheel_metadata.get_all("Tag", []),
                f"Wheel tag mismatch: {filename}")
        require(metadata["Requires-Python"], f"Missing Requires-Python: {filename}")
        if project != "typing-extensions":
            require(wheel_metadata["Root-Is-Purelib"] == "false", f"Native wheel marked pure: {filename}")
        dependencies = {normalize(re.split(r"[\s(<>=!~;\[]", value, maxsplit=1)[0])
                        for value in metadata.get_all("Requires-Dist", [])}
        expected = {"pygobject", "typing-extensions"} if project == "gst-python-binding" else set()
        if project == "pygobject":
            expected = {"pycairo"}
        require(expected <= dependencies, f"Missing dependencies: {filename}")
    return {"project": project, "version": version, "python": python,
            "requires_python": metadata["Requires-Python"],
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_sha256": hashlib.sha256(json.dumps(contents).encode()).hexdigest()}


def read_release(repository, release, download):
    tag = release["tag_name"]
    # shortcut: release tags occupy one path segment; support nested tags if the release naming policy changes.
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", tag) is not None,
            f"Unsafe release path: {tag}")
    assets = {asset["name"]: asset for asset in release["assets"]}
    require(len(assets) == len(release["assets"]), f"Duplicate assets: {tag}")
    prefix = f"https://github.com/{repository}/releases/download/{quote(tag, safe='')}/"

    def asset_data(name):
        require(name in assets, f"Missing asset in {tag}: {name}")
        require(Path(name).name == name, f"Unsafe asset name: {name}")
        url = assets[name]["browser_download_url"]
        require(url == prefix + quote(name, safe=""), f"Unexpected asset URL: {url}")
        return download(url)

    metadata_names = sorted(name for name in assets if re.fullmatch(r"py\d+\.\d+-build-metadata\.json", name))
    if not metadata_names:
        return None, "No build metadata; compatibility not verified"
    metadata = [json.loads(asset_data(name)) for name in metadata_names]
    if all("image_target" not in item and "build" not in item for item in metadata):
        return None, "Historical metadata; compatibility not verified"
    targets = {(item["image_target"], item["build_type"]) for item in metadata}
    require(len(targets) == 1, f"Mixed image targets/build types: {tag}")
    if targets != {("runtime", "release")}:
        return None, "Only release runtime images are indexed"

    first = metadata[0]
    shared = ("repository", "release_tag", "git_sha", "base_image", "base_tag",
              "build_profile", "variant", "release_revision", "requested_python_versions")
    require(first["repository"] == repository and first["release_tag"] == tag, f"Wrong provenance: {tag}")
    require(re.fullmatch(r"[a-f0-9]{40}", first["git_sha"]) is not None, f"Invalid commit: {tag}")
    require(first["base_image"] == "ubuntu" and first["base_tag"] in ("24.04", "26.04"),
            f"Unsupported distribution: {tag}")
    require(first["build_profile"] in ("minimal", "base", "full")
            and first["variant"] in ("free", "non-free"), f"Invalid profile/variant: {tag}")
    revision = first["release_revision"]
    require(type(revision) is int and revision >= 1, f"Invalid revision: {tag}")
    native_version = first["build"]["gstreamer_version"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", native_version) is not None, f"Invalid GStreamer version: {tag}")
    binding_version = native_version + (f".post{revision}" if revision > 1 else "")
    pythons = [item["python_version"] for item in metadata]
    require(len(set(pythons)) == len(pythons) and sorted(pythons) == sorted(first["requested_python_versions"]),
            f"Incomplete Python matrix: {tag}")
    files = {}
    for metadata_name, item in zip(metadata_names, metadata):
        py = item["python_version"]
        require(py in ("3.12", "3.14") and metadata_name == f"py{py}-build-metadata.json",
                f"Python metadata mismatch: {tag}")
        require(all(item[key] == first[key] for key in shared), f"Conflicting build metadata: {tag}")
        build = item["build"]
        environment = json.loads(asset_data(f"py{py}-build-environment.json"))
        require(environment == build, f"Environment/metadata mismatch: {tag}, Python {py}")
        require(build["gstreamer_version"] == native_version
                and build["gstreamer_commit"] == first["build"]["gstreamer_commit"]
                and re.fullmatch(r"[a-f0-9]{40}", build["gstreamer_commit"]) is not None,
                f"Conflicting native sources: {tag}")
        require(build["python_version"].startswith(py + ".")
                and build["profile"] == first["build_profile"]
                and build["variant"] == first["variant"]
                and build["build_type"] == "release" and build["release_revision"] == revision,
                f"Conflicting environment: {tag}, Python {py}")
        os_release = dict(line.split("=", 1) for line in build["ubuntu"].splitlines() if "=" in line)
        require(os_release["ID"].strip('"') == "ubuntu"
                and os_release["VERSION_ID"].strip('"') == first["base_tag"], f"Wrong Ubuntu: {tag}")
        requirements = {normalize(name): version for name, version in build["requirements"].items()}
        require(set(requirements) == set(PROJECTS) and requirements["gst-python-binding"] == binding_version,
                f"Unexpected requirements: {tag}")
        for project, version in requirements.items():
            python_tag = "py3-none-any" if project == "typing-extensions" else f"cp{py.replace('.', '')}-cp{py.replace('.', '')}-linux_x86_64"
            filename = f"{project.replace('-', '_')}-{version}-{python_tag}.whl"
            if project == "gst-python-binding":
                require(item["wheel_file"] == filename, f"Wrong binding wheel: {tag}")
            if filename in files:
                continue
            data = asset_data(filename)
            info = wheel_info(filename, data)
            checksum = asset_data(filename + ".sha256").decode().strip().split()
            require(checksum == [info["sha256"], filename], f"Checksum mismatch: {tag}/{filename}")
            url = assets[filename]["browser_download_url"]
            if project == "typing-extensions":
                upstream = json.loads(download(f"https://pypi.org/pypi/typing-extensions/{quote(version, safe='')}/json"))
                matches = [file for file in upstream["urls"] if file["filename"] == filename
                           and file["digests"]["sha256"] == info["sha256"] and not file["yanked"]]
                require(len(matches) == 1, f"typing-extensions differs from PyPI: {filename}")
                url = matches[0]["url"]
                require(url.startswith("https://files.pythonhosted.org/"), f"Unexpected PyPI URL: {url}")
                require(hashlib.sha256(download(url)).hexdigest() == info["sha256"], f"PyPI checksum mismatch: {filename}")
            files[filename] = info | {"url": url}
    require(set(files) == {name for name in assets if name.endswith(".whl")}, f"Unexpected wheel assets: {tag}")
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", release["published_at"]) is not None
            and type(release["id"]) is int, f"Invalid release publication metadata: {tag}")
    return {"tag": tag, "ubuntu": first["base_tag"], "gstreamer": native_version,
            "revision": revision, "profile": first["build_profile"], "variant": first["variant"],
            "commit": first["git_sha"], "gstreamer_commit": first["build"]["gstreamer_commit"],
            "published_at": release["published_at"], "release_id": release["id"],
            "pythons": sorted(pythons), "files": files}, None


def page(title, body):
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title>'
            '<style>body{font-family:system-ui,sans-serif;line-height:1.5;max-width:76rem;margin:2rem auto;padding:0 1rem}'
            'table{border-collapse:collapse;width:100%}th,td{text-align:left;vertical-align:top;padding:.5rem;border-bottom:1px solid #ddd}'
            'a{overflow-wrap:anywhere}pre{overflow-x:auto;padding:1rem;background:#f5f5f5}code{font-size:.95em}</style>'
            f'</head><body>\n{body}\n</body></html>\n')


def ubuntu_indexes(releases):
    groups = {}
    # The first publication supplies immutable asset links when later variants arrive.
    for release in sorted(releases, key=lambda item: (item["published_at"], item["release_id"])):
        ubuntu = release["ubuntu"]
        if ubuntu not in groups:
            groups[ubuntu] = {"ubuntu": ubuntu, "index": f"ubuntu-{ubuntu}/simple/",
                              "pythons": [], "files": {}, "releases": {}}
        group = groups[ubuntu]
        key = (release["gstreamer"], release["revision"])
        if key not in group["releases"]:
            group["releases"][key] = {"gstreamer": release["gstreamer"], "revision": release["revision"],
                                      "gstreamer_commit": release["gstreamer_commit"],
                                      "versions": {}, "pythons": [], "sources": []}
        snapshot = group["releases"][key]
        require(snapshot["gstreamer_commit"] == release["gstreamer_commit"],
                f'Conflicting GStreamer sources for {group["index"]}: {release["tag"]}')
        for filename, info in sorted(release["files"].items()):
            version = snapshot["versions"].setdefault(info["project"], info["version"])
            require(version == info["version"],
                    f'Conflicting dependency versions for {group["index"]}: {filename}')
            if filename in group["files"]:
                require(group["files"][filename]["content_sha256"] == info["content_sha256"],
                        f'Conflicting wheel contents for {group["index"]}: {filename} in {release["tag"]}')
            else:
                group["files"][filename] = info | {"source_release": release["tag"]}
        group["pythons"] = sorted(set(group["pythons"]) | set(release["pythons"]))
        snapshot["pythons"] = sorted(set(snapshot["pythons"]) | set(release["pythons"]))
        snapshot["sources"].append({key: release[key] for key in
                                    ("tag", "profile", "variant", "commit", "pythons")})
    for group in groups.values():
        group["releases"] = [group["releases"][key] for key in sorted(group["releases"])]
    return [groups[key] for key in sorted(groups)]


def write_simple(root, title, files):
    root.mkdir(parents=True)
    links = []
    for project in PROJECTS:
        directory = root / project
        directory.mkdir()
        wheels = []
        for filename, info in sorted(files.items()):
            if info["project"] == project:
                url = html.escape(info["url"] + "#sha256=" + info["sha256"], quote=True)
                python = html.escape(info["requires_python"], quote=True)
                wheels.append(f'<a href="{url}" data-requires-python="{python}">{html.escape(filename)}</a><br>')
        (directory / "index.html").write_text(page(project, "\n".join(wheels)), encoding="utf-8")
        links.append(f'<a href="{project}/">{project}</a><br>')
    (root / "index.html").write_text(page(title, "\n".join(links)), encoding="utf-8")


def installation_guide(repository, indexes):
    owner, project = repository.split("/")
    base_url = f"https://{owner.lower()}.github.io/{project}/"
    body = ('<h2>Install in a virtual environment</h2>'
            '<p>Use Linux amd64, the matching Ubuntu version and CPython 3.12 or 3.14. '
            'Your environment must already contain compatible GStreamer libraries, GLib, Cairo and system typelibs; '
            'the matching project image supplies these native dependencies. The wheel does not install them.</p>'
            '<p>Check <code>gst-launch-1.0 --version</code> and select the corresponding binding version. '
            'Create and activate a fresh virtual environment, then choose either pip or uv below. '
            'Each example uses a published version; change the pin to match your native installation.</p>'
            '<pre><code>python -m venv .venv\nsource .venv/bin/activate</code></pre>')
    for entry in indexes:
        url = base_url + entry["index"]
        version = entry["releases"][0]["versions"]["gst-python-binding"]
        body += f'<h3>Ubuntu {html.escape(entry["ubuntu"])}</h3>'
        for tool, option in (("python -m pip", "--index-url"), ("uv pip", "--default-index")):
            command = (f'{tool} install \\\n'
                       '    --only-binary=:all: \\\n'
                       f'    {option} "{url}" \\\n'
                       f'    "gst-python-binding=={version}"')
            body += f'<pre><code>{html.escape(command)}</code></pre>'
    body += ('<h2>Choose a version</h2>'
             '<p>Python ABI selection is automatic. Native GStreamer version selection is not: '
             'an unconstrained install can select bindings newer than the installed native libraries.</p>'
             '<table><thead><tr><th>Requirement example</th><th>Selection</th></tr></thead><tbody>'
             '<tr><td><code>==1.28.8</code></td><td>First build of GStreamer 1.28.8 bindings</td></tr>'
             '<tr><td><code>==1.28.8.post2</code></td><td>Exactly distribution revision 2, when published</td></tr>'
             '<tr><td><code>==1.28.8.*</code></td><td>Latest available revision of 1.28.8; excludes 1.28.9</td></tr>'
             '</tbody></table><p>Only versions listed in the index are available. '
             'Pin an exact binding version and lock all dependencies to reproduce an environment.</p>'
             '<h2>Packages and download origins</h2><ul>'
             '<li><code>gst-python-binding</code>: GStreamer overrides and native Python extensions, from GitHub Release assets.</li>'
             '<li><code>PyGObject</code> and <code>pycairo</code>: prebuilt wheels from the matching Ubuntu release environment.</li>'
             '<li><code>typing-extensions</code>: a link to the original, checksum-verified wheel on PyPI. '
             'Its index page lets pip resolve the dependency using this index alone; no copy is hosted on Pages.</li>'
             '</ul><p>All indexes are static HTML. Binary assets remain in GitHub Releases or on PyPI. '
             'The <a href="catalog.json">JSON catalog</a> records versions, checksums and build provenance.</p>')
    return body


def write_index(repository, releases, output, download=fetch):
    require(REPOSITORY.fullmatch(repository) is not None, "Expected owner/repository")
    require(not output.exists(), f"Output already exists: {output}")
    cache = {}

    def cached_download(url):
        if url not in cache:
            cache[url] = download(url)
        return cache[url]

    catalog, skipped = [], []
    for release in sorted(releases, key=lambda item: item["tag_name"]):
        if release["draft"] or release["prerelease"]:
            continue
        record, reason = read_release(repository, release, cached_download)
        if record:
            require(record["tag"] not in {entry["tag"] for entry in catalog}, "Duplicate release tag")
            catalog.append(record)
        else:
            skipped.append((release["tag_name"], reason))
    require(catalog, "No verified release runtime wheels found; refusing an empty deployment")
    indexes = ubuntu_indexes(catalog)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wheel-index-", dir=output.parent) as temporary:
        site = Path(temporary) / "site"
        site.mkdir()
        rows = []
        for release in indexes:
            relative = release["index"]
            write_simple(site / relative, relative, release["files"])
            versions = sorted({info["version"] for info in release["files"].values() if info["project"] == "gst-python-binding"})
            values = [release["ubuntu"], ", ".join(versions), ", ".join(release["pythons"])]
            cells = "".join(f"<td>{html.escape(value)}</td>" for value in values)
            sources = "<br>".join(
                f'<a href="https://github.com/{repository}/releases/tag/{quote(source["tag"], safe="")}">'
                f'{html.escape(source["tag"])}</a> ({html.escape(source["commit"][:12])})'
                for snapshot in release["releases"] for source in snapshot["sources"])
            rows.append(f'<tr>{cells}<td><a href="{relative}">simple/</a></td><td>{sources}</td></tr>')
        body = ('<h1>GStreamer OCI wheel indexes</h1>\n'
                '<p>Choose your Ubuntu index and pin gst-python-binding to the version matching your native GStreamer installation. '
                'These wheels do not install GStreamer or system typelibs. Only linux/amd64 release runtime builds are verified.</p>\n'
                '<p>Wheel indexes are shared across image profiles and codec variants only when all wheel contents match. '
                'Image tags and native plugin availability remain specific to each image.</p>\n'
                '<p>Python ABI is selected automatically; Ubuntu compatibility is not. '
                'typing-extensions links point to the original verified PyPI wheel.</p>\n'
                '<p>Use ==1.28.8 for the first build, ==1.28.8.post2 for revision 2, '
                'or ==1.28.8.* to allow revisions of GStreamer 1.28.8. '
                'pip and uv do not detect the installed native GStreamer version.</p>\n'
                '<table><thead><tr><th>Ubuntu</th><th>Binding versions</th><th>Python</th>'
                '<th>Simple index</th><th>Source releases and commits</th></tr></thead><tbody>\n'
                + "\n".join(rows) + '</tbody></table>\n'
                + installation_guide(repository, indexes))
        if skipped:
            body += '<h2>Not indexed</h2><ul>\n' + "\n".join(
                f"<li>{html.escape(tag)}: {html.escape(reason)}</li>" for tag, reason in skipped) + '</ul>'
        (site / "index.html").write_text(page("GStreamer OCI wheel indexes", body), encoding="utf-8")
        (site / "catalog.json").write_text(json.dumps(indexes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        site.rename(output)
    return indexes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="Public GitHub owner/repository")
    parser.add_argument("--output", type=Path, required=True, help="New directory containing only generated public files")
    args = parser.parse_args()
    require(REPOSITORY.fullmatch(args.repository) is not None, "Expected owner/repository")
    catalog = write_index(args.repository, github_releases(args.repository), args.output)
    print(f"Verified {len(catalog)} Ubuntu indexes in {args.output}")


if __name__ == "__main__":
    main()
