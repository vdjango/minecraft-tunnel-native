import ast
import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import build_native, embed_protocol_data, fetch_deps


class DependencyFallbackTests(unittest.TestCase):
    def test_libssh_official_mirror_timeout_uses_pinned_github_mirror(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def clone(arguments, **_kwargs):
                checkout = Path(arguments[-1])
                checkout.mkdir()
                (checkout / '.git').mkdir()

            item = ('libssh', fetch_deps.SOURCES['libssh'])
            with (mock.patch.object(fetch_deps, 'ROOT', root),
                  mock.patch.object(fetch_deps.urllib.request, 'urlopen', side_effect=TimeoutError()),
                  mock.patch.object(fetch_deps.subprocess, 'run', side_effect=clone) as run,
                  mock.patch.object(fetch_deps, 'git', return_value=fetch_deps.LIBSSH_MIRROR_COMMIT)):
                result = fetch_deps.fetch(item)

            self.assertIn('commit=' + fetch_deps.LIBSSH_MIRROR_COMMIT, result)
            self.assertTrue((root / '.deps/libssh-0.12.2/.git').is_dir())
            self.assertIn('https://github.com/libssh/libssh-mirror.git', run.call_args.args[0])


@unittest.skipUnless(shutil.which('cmake'), 'CMake is required for runtime staging checks')
class WindowsRuntimeStagingTests(unittest.TestCase):
    def test_actual_cmake_rule_selects_all_dlls_for_each_configuration(self):
        self.check_runtime_install(True)

    def test_install_without_separate_debug_runtime(self):
        self.check_runtime_install(False)

    def check_runtime_install(self, separate_debug):
        module = Path(__file__).resolve().parents[1] / 'cmake/StageWindowsCrypto.cmake'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'crypto stage'
            root.mkdir()
            for config in (('release', 'debug') if separate_debug else ('release',)):
                (root / config).mkdir()
                for name in ('libssl.dll', 'libcrypto.dll'):
                    (root / config / name).write_bytes((config + ':' + name).encode())
            (root / 'main.c').write_text('int main(void) { return 0; }\n')
            (root / 'CMakeLists.txt').write_text(f'''cmake_minimum_required(VERSION 3.24)
project(crypto_staging C)
include("{module.as_posix()}")
file(GLOB native_crypto_runtime "${{CMAKE_SOURCE_DIR}}/release/*.dll")
file(GLOB native_crypto_runtime_debug "${{CMAKE_SOURCE_DIR}}/debug/*.dll")
add_executable(probe main.c)
set_target_properties(probe PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${{CMAKE_BINARY_DIR}}/out/$<CONFIG>")
buggcraft_stage_windows_crypto(probe)
buggcraft_install_windows_crypto()
''', encoding='utf-8')
            build = root / 'build'
            for config in ('Release', 'Debug'):
                for command in (["cmake", "-S", str(root), "-B", str(build), f"-DCMAKE_BUILD_TYPE={config}"],
                                ["cmake", "--build", str(build), "--config", config, "--parallel", "2"]):
                    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, errors='replace', timeout=90)
                    self.assertEqual(result.returncode, 0, result.stdout)
                for name in ('libssl.dll', 'libcrypto.dll'):
                    self.assertEqual((build / 'out' / config / name).read_bytes(),
                                     ((config.lower() if separate_debug else 'release') + ':' + name).encode())
                install = root / ('installed ' + config)
                result = subprocess.run(['cmake', '--install', str(build), '--config', config,
                                         '--prefix', str(install)],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, errors='replace', timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout)
                for name in ('libssl.dll', 'libcrypto.dll'):
                    self.assertEqual((install / 'bin' / name).read_bytes(),
                                     ((config.lower() if separate_debug else 'release') + ':' + name).encode())


class ProtocolEmbeddingTests(unittest.TestCase):
    def test_literals_are_bounded_and_round_trip_bytes(self):
        data = (b'{"name":"' + bytes(range(256)) + b'"}\n') * 100
        literals = embed_protocol_data.adjacent_literals(data)
        self.assertGreater(len(literals), 1)
        self.assertTrue(all(len(literal.encode("ascii")) <= 8192 for literal in literals))
        self.assertEqual(b"".join(ast.literal_eval("b" + literal) for literal in literals), data)
        self.assertIn(r"\000", "".join(literals))
        self.assertIn(r'\"', "".join(literals))

    def test_header_has_both_compile_time_members(self):
        header = embed_protocol_data.render_header(b"{}", b"[]")
        self.assertIn("inline constexpr char profiles_json[]", header)
        self.assertIn('    "{}";', header)
        self.assertIn("inline constexpr char legacy_json[]", header)


class BuildLogTests(unittest.TestCase):
    def test_failed_build_summary_keeps_early_msvc_error(self):
        lines = ["header(1): error C2026: string too big\n"] + ["warning C4996 " + "x" * 250 + "\n"] * 120
        class FakeProcess:
            stdout = io.StringIO("".join(lines))
            def wait(self):
                return 2
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "build.log"
            with mock.patch.object(build_native.subprocess, "Popen", return_value=FakeProcess()):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = build_native.build("build", "Release", "2", log)
            self.assertEqual(status, 2)
            annotation = output.getvalue().split("::error title=Native compilation failed::", 1)[1]
            self.assertIn("error C2026", annotation)
            self.assertIn("warning C4996", log.read_text())

    def test_success_does_not_emit_error_annotation(self):
        class FakeProcess:
            stdout = io.StringIO("[100%] Built target bridge\n")
            def wait(self):
                return 0
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with mock.patch.object(build_native.subprocess, "Popen", return_value=FakeProcess()):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(build_native.build("build", "Release", "2", Path(directory) / "build.log"), 0)
            self.assertNotIn("::error", output.getvalue())

    def test_compiler_and_linker_diagnostics(self):
        for line in ("x.cpp(1): error C2026: too big", "fatal error C1083: missing header",
                     "x.cpp:12:4: error: unknown type", "LINK : fatal error LNK1104: missing file",
                     "undefined reference to `main'", "CMake Error at CMakeLists.txt:1"):
            with self.subTest(line=line):
                self.assertTrue(build_native.is_error(line))
        self.assertFalse(build_native.is_error("warning C4996: deprecated"))

    def test_github_annotation_escaping(self):
        self.assertEqual(build_native.github_escape("50%\r\nerror"), "50%25%0D%0Aerror")


if __name__ == "__main__":
    unittest.main()
