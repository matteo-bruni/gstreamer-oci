import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
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

    def release(self, ubuntu="24.04", revision=1):
        tag = f"gst-python-binding-1.28.8-ubuntu-{ubuntu}-base-non-free"
        if revision > 1:
            tag += f"-r{revision}"
        release = {"tag_name": tag, "draft": False, "prerelease": False, "assets": []}
        versions = {"gst-python-binding": "1.28.8" + (f".post{revision}" if revision > 1 else ""),
                    "PyGObject": "3.58.1", "pycairo": "1.29.2", "typing-extensions": "4.15.0"}

        def asset(name, data):
            url = f"https://github.com/example/repo/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"
            self.downloads[url] = data
            if not any(item["name"] == name for item in release["assets"]):
                release["assets"].append({"name": name, "browser_download_url": url})

        for py in ("3.12", "3.14"):
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
                    wheel.writestr(directory + "METADATA", f"Metadata-Version: 2.4\nName: {project}\nVersion: {version}\nRequires-Python: >=3.12\n{dependencies}")
                    wheel.writestr(directory + "WHEEL", f"Wheel-Version: 1.0\nRoot-Is-Purelib: {str(pure).lower()}\nTag: {abi}\n")
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
            build = {"gstreamer_version": "1.28.8", "gstreamer_commit": "b" * 40,
                     "python_version": py + ".1", "ubuntu": f'ID=ubuntu\nVERSION_ID="{ubuntu}"\n',
                     "profile": "base", "variant": "non-free", "build_type": "release",
                     "release_revision": revision, "requirements": versions}
            metadata = {"repository": "example/repo", "release_tag": tag, "git_sha": "a" * 40,
                        "base_image": "ubuntu", "base_tag": ubuntu, "build_profile": "base",
                        "variant": "non-free", "release_revision": revision,
                        "requested_python_versions": ["3.12", "3.14"], "image_target": "runtime",
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
            for release in releases:
                root = first / "releases" / release["tag_name"] / "simple"
                binding = (root / "gst-python-binding/index.html").read_text()
                self.assertIn("cp312-cp312", binding)
                self.assertIn("cp314-cp314", binding)
                self.assertIn("#sha256=", binding)
                self.assertIn('data-requires-python="&gt;=3.12"', binding)
                self.assertIn(release["tag_name"], binding)
                other = releases[1] if release == releases[0] else releases[0]
                self.assertNotIn(other["tag_name"], binding)
                typing = (root / "typing-extensions/index.html").read_text()
                self.assertIn("https://files.pythonhosted.org/", typing)
                self.assertNotIn("https://github.com/", typing)
                self.assertEqual(typing.count("<a "), 1)

    def test_revision_uses_post_version_and_distinct_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = self.generate([self.release(), self.release(revision=2)], Path(tmp) / "site")
            self.assertEqual([item["revision"] for item in catalog], [1, 2])
            self.assertTrue(all(info["version"] == "1.28.8.post2" for info in catalog[1]["files"].values()
                                if info["project"] == "gst-python-binding"))

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


if __name__ == "__main__":
    unittest.main()
