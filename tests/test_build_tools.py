import ast
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import build_native, embed_protocol_data


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
