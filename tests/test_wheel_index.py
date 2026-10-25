import copy
import hashlib
import html
import importlib.util
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from urllib.parse import quote
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("wheel_index", ROOT / "scripts/generate-wheel-index.py")
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)


class WheelIndexTests(unittest.TestCase):
    def setUp(self):
        self.downloads = {}
        self.release_id = 0

    def release(self, ubuntu="24.04", revision=1, profile="base", variant="non-free",
                pythons=("3.12", "3.14"), dependencies=None, timestamp=(2026, 10, 25, 18, 0, 0),
                gstreamer="1.28.8"):
        tag = f"gst-python-binding-{gstreamer}-ubuntu-{ubuntu}-{profile}"
        if variant == "non-free":
            tag += "-non-free"
        if revision > 1:
            tag += f"-r{revision}"
        self.release_id += 1
        release = {"tag_name": tag, "draft": False, "prerelease": False, "assets": [],
                   "published_at": "2026-10-25T18:00:00Z", "id": self.release_id}
        versions = {"gst-python-binding": gstreamer + (f".post{revision}" if revision > 1 else ""),
                    "PyGObject": "3.58.1", "pycairo": "1.29.2", "typing-extensions": "4.15.0"}
        versions.update(dependencies or {})

        def asset(name, data):
            url = f"https://github.com/example/repo/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"
            self.downloads[url] = data
            if not any(item["name"] == name for item in release["assets"]):
                release["assets"].append({"name": name, "browser_download_url": url})

        for py in pythons:
            for project, version in versions.items():
                pure = project == "typing-extensions"
                abi = "py3-none-any" if pure else f"cp{py.replace('.', '')}-cp{py.replace('.', '')}-linux_x86_64"
                filename = f"{index.normalize(project).replace('-', '_')}-{version}-{abi}.whl"
                directory = f"{index.normalize(project).replace('-', '_')}-{version}.dist-info/"
                dependencies = ""
                if project == "gst-python-binding":
                    dependencies = "Requires-Dist: PyGObject>=3.56.3\nRequires-Dist: typing-extensions>=4.15.0\n"
                if project == "PyGObject":
                    dependencies = "Requires-Dist: pycairo>=1.16\n"
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as wheel:
                    date = (2026, 10, 25, 18, 0, 0) if pure else timestamp
                    wheel.writestr(zipfile.ZipInfo(directory + "METADATA", date), f"Metadata-Version: 2.4\nName: {project}\nVersion: {version}\nRequires-Python: >=3.12\n{dependencies}")
                    wheel.writestr(zipfile.ZipInfo(directory + "WHEEL", date), f"Wheel-Version: 1.0\nRoot-Is-Purelib: {str(pure).lower()}\nTag: {abi}\n")
                binary = data.getvalue()
                digest = hashlib.sha256(binary).hexdigest()
                asset(filename, binary)
                asset(filename + ".sha256", f"{digest}  {filename}\n".encode())
                if pure:
                    url = f"https://files.pythonhosted.org/packages/{filename}"
                    self.downloads[url] = binary
                    self.downloads[f"https://pypi.org/pypi/typing-extensions/{version}/json"] = json.dumps({
                        "urls": [{"filename": filename, "digests": {"sha256": digest}, "yanked": False, "url": url}]
                    }).encode()
            build = {"gstreamer_version": gstreamer, "gstreamer_commit": "b" * 40,
                     "python_version": py + ".1", "ubuntu": f'ID=ubuntu\nVERSION_ID="{ubuntu}"\n',
                     "profile": profile, "variant": variant, "build_type": "release",
                     "release_revision": revision, "requirements": versions}
            metadata = {"repository": "example/repo", "release_tag": tag, "git_sha": "a" * 40,
                        "base_image": "ubuntu", "base_tag": ubuntu, "build_profile": profile,
                        "variant": variant, "release_revision": revision,
                        "requested_python_versions": list(pythons), "image_target": "runtime",
                        "build_type": "release", "python_version": py, "build": build,
                        "wheel_file": f"gst_python_binding-{versions['gst-python-binding']}-cp{py.replace('.', '')}-cp{py.replace('.', '')}-linux_x86_64.whl"}
            asset(f"py{py}-build-environment.json", json.dumps(build).encode())
            asset(f"py{py}-build-metadata.json", json.dumps(metadata).encode())
        return release

    def metadata_url(self, release, py="3.12"):
        return next(asset["browser_download_url"] for asset in release["assets"]
                    if asset["name"] == f"py{py}-build-metadata.json")

    def generate(self, releases, output):
        return index.write_index("example/repo", releases, output, self.downloads.__getitem__)

    def test_two_ubuntu_indexes_are_deterministic_and_keep_native_wheels_separate(self):
        releases = [self.release(), self.release("26.04")]
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            catalog = self.generate(releases, first)
            self.generate(list(reversed(releases)), second)
            content = lambda directory: {str(path.relative_to(directory)): path.read_bytes()
                                         for path in directory.rglob("*") if path.is_file()}
            self.assertEqual(content(first), content(second))
            self.assertEqual(len(catalog), 2)
            self.assertEqual(len(content(first)), 12)
            self.assertFalse((first / "releases").exists())
            self.assertFalse((first / "wheels").exists())
            for release, record in zip(releases, catalog):
                root = first / record["index"]
                binding = (root / "gst-python-binding/index.html").read_text()
                self.assertIn("cp312-cp312", binding)
                self.assertIn("cp314-cp314", binding)
                self.assertIn("#sha256=", binding)
                self.assertIn('data-requires-python="&gt;=3.12"', binding)
                self.assertIn(release["tag_name"], binding)
                other = releases[1] if release == releases[0] else releases[0]
                self.assertNotIn(other["tag_name"], binding)
                self.assertFalse((first / f'ubuntu-{record["ubuntu"]}/1.28.8').exists())
                typing = (root / "typing-extensions/index.html").read_text()
                self.assertIn("https://files.pythonhosted.org/", typing)
                self.assertNotIn("https://github.com/", typing)
                self.assertEqual(typing.count("<a "), 1)

    def test_additional_native_metadata_and_older_assets_remain_compatible(self):
        releases = [self.release(), self.release('26.04')]
        for py in ('3.12', '3.14'):
            url = self.metadata_url(releases[1], py)
            metadata = json.loads(self.downloads[url])
            metadata['build'].update({
                'native_dependencies': {'glib': {'build': '2.88.0', 'runtime': '2.88.0'}},
                'build_tools': {'meson': '1.12.1', 'c_compiler': {'id': 'gcc', 'version': '15.2.0'}},
            })
            self.downloads[url] = json.dumps(metadata).encode()
            environment_url = next(asset['browser_download_url'] for asset in releases[1]['assets']
                                   if asset['name'] == f'py{py}-build-environment.json')
            self.downloads[environment_url] = json.dumps(metadata['build']).encode()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(len(self.generate(releases, Path(tmp) / 'site')), 2)

    def test_revisions_share_ubuntu_index_with_distinct_post_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "site"
            catalog = self.generate([self.release(), self.release(revision=2)], output)
            self.assertEqual(len(catalog), 1)
            self.assertEqual([item["revision"] for item in catalog[0]["releases"]], [1, 2])
            versions = {info["version"] for info in catalog[0]["files"].values() if info["project"] == "gst-python-binding"}
            self.assertEqual(versions, {"1.28.8", "1.28.8.post2"})
            self.assertEqual(len(catalog[0]["files"]), 9)
            self.assertIn("1.28.8.post2", (output / "ubuntu-24.04/simple/gst-python-binding/index.html").read_text())

    def test_draft_prerelease_dev_debug_and_legacy_are_not_indexed(self):
        good = self.release()
        for mode in ("draft", "prerelease", "dev", "debug", "legacy"):
            release = self.release("26.04")
            if mode in ("draft", "prerelease"):
                release[mode] = True
            else:
                for py in ("3.12", "3.14"):
                    url = self.metadata_url(release, py)
                    metadata = json.loads(self.downloads[url])
                    if mode == "legacy":
                        metadata = {"python_version": py}
                    elif mode == "dev":
                        metadata["image_target"] = "dev"
                    else:
                        metadata["build_type"] = "debug"
                    self.downloads[url] = json.dumps(metadata).encode()
            # Process each fixture before replacing its shared download URLs.
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "site"
                catalog = self.generate([good, release], output)
                self.assertEqual(len(catalog), 1)
                if mode not in ("draft", "prerelease"):
                    self.assertIn("Not indexed", (output / "index.html").read_text())

    def test_bad_checksum_or_missing_asset_aborts_without_output(self):
        for mode in ("checksum", "missing", "duplicate", "extra"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                release = self.release()
                if mode == "checksum":
                    asset = next(item for item in release["assets"] if item["name"].endswith(".sha256"))
                    self.downloads[asset["browser_download_url"]] = ("0" * 64 + "  " + asset["name"][:-7]).encode()
                elif mode == "missing":
                    release["assets"] = [item for item in release["assets"] if item["name"] != "py3.14-build-environment.json"]
                elif mode == "duplicate":
                    release["assets"].append(copy.deepcopy(release["assets"][0]))
                else:
                    release["assets"].append({"name": "unexpected.whl", "browser_download_url": "unused"})
                output = Path(tmp) / "site"
                with self.assertRaises(ValueError):
                    self.generate([release], output)
                self.assertFalse(output.exists())

    def test_metadata_conflicts_incomplete_python_or_wrong_ubuntu_fail(self):
        for field, value in (("git_sha", "c" * 40), ("release_revision", 2),
                             ("requested_python_versions", ["3.12"]), ("base_tag", "26.04"),
                             ("release_tag", "wrong"), ("repository", "wrong/repo")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                release = self.release()
                url = self.metadata_url(release)
                metadata = json.loads(self.downloads[url])
                metadata[field] = value
                self.downloads[url] = json.dumps(metadata).encode()
                with self.assertRaises(ValueError):
                    self.generate([release], Path(tmp) / "site")

    def test_untrusted_asset_url_and_path_are_rejected(self):
        for mode in ("url", "path"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                release = self.release()
                if mode == "path":
                    release["tag_name"] = "../outside"
                else:
                    asset = next(item for item in release["assets"] if item["name"] == "py3.12-build-metadata.json")
                    asset["browser_download_url"] = "https://example.org/stolen"
                with self.assertRaises(ValueError):
                    self.generate([release], Path(tmp) / "site")

    def test_pypi_must_match_published_typing_wheel(self):
        release = self.release()
        url = "https://pypi.org/pypi/typing-extensions/4.15.0/json"
        data = json.loads(self.downloads[url])
        data["urls"][0]["digests"]["sha256"] = "0" * 64
        self.downloads[url] = json.dumps(data).encode()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self.generate([release], Path(tmp) / "site")

    def test_wheel_filename_and_metadata_must_agree(self):
        release = self.release()
        asset = next(item for item in release["assets"] if item["name"].startswith("gst_python_binding-")
                     and item["name"].endswith(".whl"))
        with self.assertRaises(ValueError):
            index.wheel_info(asset["name"].replace("1.28.8", "1.28.9"), self.downloads[asset["browser_download_url"]])
        with self.assertRaises(ValueError):
            index.wheel_info(asset["name"].replace("linux_x86_64", "manylinux_2_28_x86_64"), self.downloads[asset["browser_download_url"]])

    def test_empty_catalog_or_existing_site_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "site"
            with self.assertRaises(ValueError):
                self.generate([], output)
            output.mkdir()
            sentinel = output / "index.html"
            sentinel.write_text("previous site")
            with self.assertRaises(ValueError):
                self.generate([self.release()], output)
            self.assertEqual(sentinel.read_text(), "previous site")

    def test_profiles_and_variants_share_index_without_old_paths(self):
        baseline = self.release()
        full = self.release(profile="full", variant="free", timestamp=(2026, 10, 26, 12, 0, 0))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "site"
            first = self.generate([baseline], output)
            second = self.generate([full, baseline], Path(tmp) / "more")
            self.assertEqual(len(second), 1)
            self.assertEqual(first[0]["files"], second[0]["files"])
            self.assertEqual({source["tag"] for source in second[0]["releases"][0]["sources"]},
                             {baseline["tag_name"], full["tag_name"]})
            canonical = Path(tmp) / "more" / "ubuntu-24.04/simple/pygobject/index.html"
            self.assertIn(baseline["tag_name"], canonical.read_text())
            self.assertNotIn(full["tag_name"], canonical.read_text())
            self.assertFalse((Path(tmp) / "more/releases").exists())
            self.assertEqual((output / first[0]["index"] / "pygobject/index.html").read_bytes(), canonical.read_bytes())

    def test_all_gstreamer_versions_share_one_index_per_ubuntu(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = self.generate([self.release(), self.release(gstreamer="1.28.9"),
                                     self.release("26.04")], Path(tmp) / "site")
            self.assertEqual([entry["index"] for entry in catalog],
                             ["ubuntu-24.04/simple/", "ubuntu-26.04/simple/"])
            versions = {info["version"] for info in catalog[0]["files"].values() if info["project"] == "gst-python-binding"}
            self.assertEqual(versions, {"1.28.8", "1.28.9"})

    def test_separate_python_publications_merge_without_changing_existing_links(self):
        first = self.release(pythons=("3.12",))
        second = self.release(profile="full", pythons=("3.14",))
        with tempfile.TemporaryDirectory() as tmp:
            before = self.generate([first], Path(tmp) / "first")[0]
            after = self.generate([second, first], Path(tmp) / "second")[0]
            self.assertEqual(after["pythons"], ["3.12", "3.14"])
            for filename, info in before["files"].items():
                self.assertEqual(info, after["files"][filename])
            self.assertEqual(len(after["files"]), 7)

    def test_conflicting_native_sources_or_dependency_versions_abort_merge(self):
        for conflict in ("sources", "dependency"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as tmp:
                first = self.release()
                second = self.release(profile="full", dependencies={"PyGObject": "3.58.2"} if conflict == "dependency" else None)
                if conflict == "sources":
                    for py in ("3.12", "3.14"):
                        url = self.metadata_url(second, py)
                        metadata = json.loads(self.downloads[url])
                        metadata["build"]["gstreamer_commit"] = "c" * 40
                        self.downloads[url] = json.dumps(metadata).encode()
                        environment_url = url.replace("-build-metadata.json", "-build-environment.json")
                        self.downloads[environment_url] = json.dumps(metadata["build"]).encode()
                output = Path(tmp) / "site"
                with self.assertRaisesRegex(ValueError, "Conflicting"):
                    self.generate([first, second], output)
                self.assertFalse(output.exists())

    def test_different_wheel_contents_or_permissions_abort_merge(self):
        for conflict in ("binary", "metadata", "permissions"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as tmp:
                first = self.release()
                second = self.release(profile="minimal")
                asset = next(item for item in second["assets"] if item["name"].startswith("gst_python_binding-")
                             and item["name"].endswith(".whl"))
                url = asset["browser_download_url"]
                data = io.BytesIO()
                with zipfile.ZipFile(io.BytesIO(self.downloads[url])) as original, zipfile.ZipFile(data, "w") as changed:
                    for info in original.infolist():
                        content = original.read(info)
                        if info.filename.endswith("/METADATA"):
                            if conflict == "metadata":
                                content += b"Summary: changed\n"
                            if conflict == "permissions":
                                info.external_attr ^= 0o111 << 16
                        changed.writestr(info, content)
                    if conflict == "binary":
                        changed.writestr("gi/overrides/extra.so", b"different binary")
                self.downloads[url] = data.getvalue()
                self.downloads[url + ".sha256"] = (hashlib.sha256(data.getvalue()).hexdigest() + "  " + asset["name"]).encode()
                with self.assertRaisesRegex(ValueError, "Conflicting wheel contents"):
                    self.generate([first, second], Path(tmp) / "site")

    def test_dependency_collision_across_gstreamer_versions_aborts(self):
        first = self.release()
        second = self.release(gstreamer="1.28.9")
        asset = next(item for item in second["assets"] if item["name"].startswith("pygobject-")
                     and item["name"].endswith(".whl"))
        url = asset["browser_download_url"]
        data = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.downloads[url])) as original, zipfile.ZipFile(data, "w") as changed:
            for info in original.infolist():
                changed.writestr(info, original.read(info))
            changed.writestr("gi/new.so", b"different native build with the same package version")
        self.downloads[url] = data.getvalue()
        self.downloads[url + ".sha256"] = (hashlib.sha256(data.getvalue()).hexdigest() + "  " + asset["name"]).encode()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "Conflicting wheel contents"):
            self.generate([first, second], Path(tmp) / "site")

    def test_new_dependency_versions_can_coexist_in_different_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = self.generate([self.release(), self.release(revision=2, dependencies={"PyGObject": "3.58.2"})],
                                    Path(tmp) / "site")
            self.assertEqual({info["version"] for info in catalog[0]["files"].values() if info["project"] == "pygobject"},
                             {"3.58.1", "3.58.2"})

    def test_homepage_commands_match_ubuntu_indexes_and_available_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site"
            catalog = self.generate([self.release(), self.release("26.04")], site)
            homepage = (site / "index.html").read_text()
            commands = [html.unescape(value) for value in re.findall(r'<pre><code>(.*?)</code></pre>', homepage, re.DOTALL)]
            self.assertEqual(len(commands), 5)
            for command in commands:
                result = subprocess.run(['bash', '-n'], input=command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            for entry in catalog:
                url = 'https://example.github.io/repo/' + entry['index']
                matching = [shlex.split(command.replace('\\\n', '')) for command in commands if url in command]
                self.assertEqual(len(matching), 2)
                self.assertEqual({args[0] for args in matching}, {'python', 'uv'})
                for args in matching:
                    option = '--index-url' if args[0] == 'python' else '--default-index'
                    self.assertEqual(args[args.index(option) + 1], url)
                    self.assertIn('--only-binary=:all:', args)
                    project, version = args[-1].split('==')
                    self.assertEqual(project, 'gst-python-binding')
                    self.assertTrue(any(info['version'] == version and info['project'] == project
                                        for info in entry['files'].values()))
            for topic in ('system typelibs', 'Python ABI', '==1.28.8.post2', '==1.28.8.*',
                          'original, checksum-verified wheel on PyPI'):
                self.assertIn(topic, homepage)


if __name__ == "__main__":
    unittest.main()
