#!/usr/bin/env python3
"""Refuse skipped, repeated or older distribution revisions before publication."""

import argparse
import json
import re
import subprocess
import sys


TAG = re.compile(
    r"gst-python-binding-(\d+\.\d+\.\d+)-(ubuntu)-(24\.04|26\.04)-(minimal|base|full)"
    r"(?:-(debugoptimized|debug))?(-dev)?(-non-free)?(?:-r([1-9]\d*))?"
)


def canonical_identity(tag):
    match = TAG.fullmatch(tag)
    if not match:
        return None
    version, base, ubuntu, profile, build_type, dev, non_free, revision = match.groups()
    return ((version, base, ubuntu, profile, "non-free" if non_free else "free",
             "dev" if dev else "runtime", build_type or "release"), int(revision or 1))


def release_identity(repository, release, download):
    tag = release["tag_name"]
    canonical = canonical_identity(tag)
    assets = [asset for asset in release["assets"]
              if re.fullmatch(r"py\d+\.\d+(?:\.\d+)?-build-metadata\.json", asset["name"])]
    identities = []
    for asset in assets:
        item = download(asset)
        if "release_revision" not in item:
            if "build" in item or "image_target" in item:
                raise ValueError(f"Incomplete modern build metadata: {tag}")
            if canonical is None:
                raise ValueError(f"Cannot identify historical release: {tag}")
            configuration, revision = canonical
            # Old metadata lacks target/revision; only an unambiguous canonical tag is accepted.
            expected = dict(zip(("gstreamer_version", "base_image", "base_tag", "build_profile",
                                 "variant", "image_target", "build_type"), configuration))
            expected.update(repository=repository, release_tag=tag)
            for key, value in expected.items():
                if key in item and item[key] != value:
                    raise ValueError(f"Conflicting historical metadata: {tag}, {key}")
            if "non_free" in item:
                non_free = item["non_free"]
                if type(non_free) is str and non_free in ("true", "false"):
                    non_free = non_free == "true"
                if type(non_free) is not bool or non_free != (configuration[4] == "non-free"):
                    raise ValueError(f"Conflicting historical variant: {tag}")
            identity = canonical
        else:
            configuration = (item["build"]["gstreamer_version"], item["base_image"], item["base_tag"],
                             item["build_profile"], item["variant"], item["image_target"], item["build_type"])
            revision = item["release_revision"]
            if (type(revision) is not int or revision < 1
                    or item["repository"] != repository or item["release_tag"] != tag):
                raise ValueError(f"Invalid revision/provenance: {tag}")
            if (re.fullmatch(r"\d+\.\d+\.\d+", configuration[0]) is None
                    or configuration[1] != "ubuntu" or configuration[2] not in ("24.04", "26.04")
                    or configuration[3] not in ("minimal", "base", "full")
                    or configuration[4] not in ("free", "non-free")
                    or configuration[5] not in ("runtime", "dev")
                    or configuration[6] not in ("release", "debug", "debugoptimized")):
                raise ValueError(f"Invalid release configuration: {tag}")
            for key, value in (("profile", configuration[3]), ("variant", configuration[4]),
                               ("build_type", configuration[6]), ("release_revision", revision)):
                if key in item["build"] and item["build"][key] != value:
                    raise ValueError(f"Conflicting nested build metadata: {tag}, {key}")
            identity = configuration, revision
            if canonical is not None and identity != canonical:
                raise ValueError(f"Tag and metadata disagree: {tag}")
        identities.append(identity)
    if not identities:
        if canonical is None:
            raise ValueError(f"Cannot identify release without metadata: {tag}")
        return canonical
    if any(identity != identities[0] for identity in identities):
        raise ValueError(f"Conflicting Python build metadata: {tag}")
    return identities[0]


def check_revision(repository, releases, configuration, requested, download):
    published, drafts = set(), set()
    for release in releases:
        current, revision = release_identity(repository, release, download)
        if current != configuration:
            continue
        occupied = drafts if release["draft"] else published
        if revision in occupied:
            raise ValueError(f"Multiple releases occupy revision r{revision}")
        occupied.add(revision)
    if published & drafts:
        raise ValueError("Published releases and drafts occupy the same revision")
    if sorted(published) != list(range(1, len(published) + 1)):
        raise ValueError("Published revision history has gaps; repair it before publishing")
    latest = max(published, default=0)
    allowed = latest + 1
    if requested != allowed:
        previous = f"r{latest}" if latest else "none"
        raise ValueError(f"Requested revision r{requested}; last published {previous}; allowed revision r{allowed}")
    if requested in drafts:
        raise ValueError(f"Revision r{requested} is reserved by a draft; recover it before publishing")


def gh_json(*args):
    return json.loads(subprocess.check_output(["gh", "api", *args], text=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("configuration", nargs=7, metavar="OPTION")
    parser.add_argument("revision", type=int)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository) or args.revision < 1:
        parser.error("Expected owner/repository and a positive revision")
    pages = gh_json("--paginate", "--slurp", f"repos/{args.repository}/releases?per_page=100")

    def download(asset):
        # Use authenticated asset reads so retained drafts are checked too.
        return gh_json("-H", "Accept: application/octet-stream",
                       f"repos/{args.repository}/releases/assets/{asset['id']}")

    check_revision(args.repository, [release for page in pages for release in page],
                   tuple(args.configuration), args.revision, download)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"Release revision check failed: {error}", file=sys.stderr)
        sys.exit(1)
