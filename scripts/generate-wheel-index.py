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
            "sha256": hashlib.sha256(data).hexdigest()}


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
    return {"tag": tag, "ubuntu": first["base_tag"], "gstreamer": native_version,
            "revision": revision, "profile": first["build_profile"], "variant": first["variant"],
            "commit": first["git_sha"], "pythons": sorted(pythons), "files": files}, None


def page(title, body):
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)}</title></head><body>\n{body}\n</body></html>\n')


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
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wheel-index-", dir=output.parent) as temporary:
        site = Path(temporary) / "site"
        site.mkdir()
        rows = []
        for release in catalog:
            relative = f"releases/{release['tag']}/simple/"
            root = site / relative
            root.mkdir(parents=True)
            links = []
            for project in PROJECTS:
                directory = root / project
                directory.mkdir()
                wheels = []
                for filename, info in sorted(release["files"].items()):
                    if info["project"] == project:
                        url = html.escape(info["url"] + "#sha256=" + info["sha256"], quote=True)
                        python = html.escape(info["requires_python"], quote=True)
                        wheels.append(f'<a href="{url}" data-requires-python="{python}">{html.escape(filename)}</a><br>')
                (directory / "index.html").write_text(page(project, "\n".join(wheels)), encoding="utf-8")
                links.append(f'<a href="{project}/">{project}</a><br>')
            (root / "index.html").write_text(page(release["tag"], "\n".join(links)), encoding="utf-8")
            values = [release["ubuntu"], release["gstreamer"], str(release["revision"]),
                      ", ".join(release["pythons"]), release["profile"], release["variant"], release["commit"]]
            cells = "".join(f"<td>{html.escape(value)}</td>" for value in values)
            rows.append(f'<tr>{cells}<td><a href="{relative}">{html.escape(release["tag"])}</a></td></tr>')
        body = ('<h1>GStreamer OCI wheel indexes</h1>\n'
                '<p>Choose the exact Ubuntu and release matching your native GStreamer installation. '
                'These wheels do not install GStreamer or system typelibs. Only linux/amd64 release runtime builds are verified.</p>\n'
                '<p>Python ABI is selected automatically; Ubuntu compatibility is not. '
                'typing-extensions links point to the original verified PyPI wheel.</p>\n'
                '<table><thead><tr><th>Ubuntu</th><th>GStreamer</th><th>Revision</th><th>Python</th>'
                '<th>Profile</th><th>Variant</th><th>Source commit</th><th>Simple index</th></tr></thead><tbody>\n'
                + "\n".join(rows) + '</tbody></table>\n')
        if skipped:
            body += '<h2>Not indexed</h2><ul>\n' + "\n".join(
                f"<li>{html.escape(tag)}: {html.escape(reason)}</li>" for tag, reason in skipped) + '</ul>'
        (site / "index.html").write_text(page("GStreamer OCI wheel indexes", body), encoding="utf-8")
        (site / "catalog.json").write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        site.rename(output)
    return catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="Public GitHub owner/repository")
    parser.add_argument("--output", type=Path, required=True, help="New directory containing only generated public files")
    args = parser.parse_args()
    require(REPOSITORY.fullmatch(args.repository) is not None, "Expected owner/repository")
    catalog = write_index(args.repository, github_releases(args.repository), args.output)
    print(f"Verified {len(catalog)} release indexes in {args.output}")


if __name__ == "__main__":
    main()
