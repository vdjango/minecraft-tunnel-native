from pathlib import Path
import json
import os
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

from scripts import release_assets


class ReleaseAssetsTests(unittest.TestCase):
    def test_stable_prerelease_and_development_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "CMakeLists.txt"
            project.write_text("project(buggcraft_native VERSION 1.2.3 LANGUAGES C CXX)")
            self.assertEqual(release_assets.versions(project, "v1.2.3"), ("1.2.3", "1.2.3"))
            self.assertEqual(release_assets.versions(project, "v1.2.3-rc.2"), ("1.2.3-rc.2", "1.2.3~rc.2"))
            self.assertEqual(release_assets.versions(project, run_number="42"), ("1.2.3-dev.42", "1.2.3~dev.42"))
            for tag in ("v9.9.9", "v1.2.3;echo", "v1.2.3/../../x", "not-a-version"):
                with self.subTest(tag=tag), self.assertRaises(ValueError):
                    release_assets.versions(project, tag)

    def make_runtime(self, directory, windows=False):
        runtime = Path(directory) / "runtime"
        for name in (["bin/buggcraft-core.exe", "bin/buggcraft-bridge.exe", "bin/buggcraft_bridge.dll"] if windows else
                     ["bin/buggcraft-core", "bin/buggcraft-bridge", "lib/libbuggcraft_bridge.so"]):
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test runtime\x00\xff")
            path.chmod(0o755)
        for name in ("include/buggcraft/bridge.h", "share/buggcraft-native/LICENSE"):
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test license/header")
        return runtime

    def test_windows_archive_keeps_runtime_layout_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.make_runtime(directory, windows=True)
            with mock.patch.object(release_assets.subprocess, "check_output", return_value="a" * 40):
                archive = release_assets.package(runtime, Path(directory) / "output", "windows-2022", "1.2.3")
            with zipfile.ZipFile(archive) as zipped:
                self.assertIn("runtime/bin/buggcraft_bridge.dll", zipped.namelist())
                self.assertEqual(zipped.read("runtime/bin/buggcraft_bridge.dll"), b"test runtime\x00\xff")
            self.assertEqual(archive.with_name(archive.name + ".sha256").read_text().split()[0], release_assets.digest(archive))

    @unittest.skipUnless(os.name == "posix", "Unix file permissions require a Unix filesystem")
    def test_unix_archive_preserves_executable_mode_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.make_runtime(directory)
            (runtime / "lib/libexample.so.1").write_bytes(b"shared library")
            (runtime / "lib/libexample.so").symlink_to("libexample.so.1")
            with mock.patch.object(release_assets.subprocess, "check_output", return_value="a" * 40):
                archive = release_assets.package(runtime, Path(directory) / "output", "ubuntu-24.04", "1.2.3")
            with tarfile.open(archive) as tar:
                member = tar.getmember("runtime/bin/buggcraft-core")
                self.assertTrue(member.mode & 0o100)
                self.assertEqual(tar.extractfile(member).read(), b"test runtime\x00\xff")
                link = tar.getmember("runtime/lib/libexample.so")
                self.assertTrue(link.issym())
                self.assertEqual(link.linkname, "libexample.so.1")

    def test_incomplete_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "Incomplete"):
            release_assets.package(Path(directory), Path(directory) / "output", "windows-2022", "1.2.3")

    def test_macos_arm_archive_uses_supported_runner_and_dylib(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self.make_runtime(directory)
            (runtime / "lib/libbuggcraft_bridge.so").rename(runtime / "lib/libbuggcraft_bridge.dylib")
            with mock.patch.object(release_assets.subprocess, "check_output", return_value="a" * 40):
                archive = release_assets.package(runtime, Path(directory) / "output", "macos-15", "1.2.3")
            self.assertEqual(archive.name, "buggcraft-native-1.2.3-macos-arm64.tar.gz")
            with tarfile.open(archive) as tar:
                self.assertEqual(tar.extractfile("runtime/lib/libbuggcraft_bridge.dylib").read(), b"test runtime\x00\xff")
                info = json.load(tar.extractfile("runtime/share/buggcraft-native/build-info.json"))
                self.assertEqual(info["platform"], "macos-arm64")

    def test_verify_requires_all_platforms_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            names = ["buggcraft-native-1.2.3-" + platform + (".zip" if platform == "windows-x64" else ".tar.gz")
                     for platform in release_assets.PLATFORMS.values()]
            names.append("buggcraft-node_1.2.3_amd64.deb")
            for name in names:
                path = output / name
                path.write_bytes(name.encode())
                path.with_name(name + ".sha256").write_text(f"{release_assets.digest(path)}  {name}\n")
            release_assets.verify_assets(output, "1.2.3")
            self.assertEqual(len((output / "SHA256SUMS").read_text().splitlines()), 5)
            (output / names[0]).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                release_assets.verify_assets(output, "1.2.3")
            (output / names[0]).unlink()
            with self.assertRaisesRegex(ValueError, "Missing release asset"):
                release_assets.verify_assets(output, "1.2.3")


if __name__ == "__main__":
    unittest.main()
