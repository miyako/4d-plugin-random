"""macOS engine-boundary regressions; no plugin build/release phases are run."""

import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("RANDOM_OWNERSHIP_SOURCE_ROOT", ROOT))


@unittest.skipUnless(platform.system() == "Darwin", "macOS CoreFoundation host shim")
class OwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="random-ownership-")
        cls.binary = Path(cls.temp.name) / "ownership"
        command = [
            "xcrun", "clang++", "-std=c++11", "-g", "-O1",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-Wno-deprecated-declarations", "-Wno-writable-strings",
            "-I", str(SOURCE / "4D Plugin API"),
            "-I", str(SOURCE / "4D Plugin API/Classes"),
            "-I", str(SOURCE / "support"),
            "-I", str(SOURCE),
            str(ROOT / ".github/tests/ownership_host.mm"),
            "-framework", "Cocoa", "-framework", "CoreGraphics",
            "-framework", "Security",
            "-o", str(cls.binary),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_host(self, name):
        for empty in ("allocated", "null"):
            with self.subTest(empty=empty):
                result = subprocess.run(
                    [str(self.binary), name, empty], capture_output=True, text=True,
                    env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0",
                         "UBSAN_OPTIONS": "halt_on_error=1"}, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("ownership host passed", result.stdout)

    def test_string_setters(self):
        for overload in range(4):
            with self.subTest(overload=overload):
                self.run_host(f"setter{overload}")

    def test_owning_property_results(self):
        for getter in range(4):
            with self.subTest(getter=getter):
                self.run_host(f"getter{getter}")

    def test_array_transfers_and_borrowed_copies(self):
        self.run_host("arrays")

    def test_synchronous_stringify_cleanup(self):
        self.run_host("stringify")

    def test_command_reachable_size_mismatch(self):
        self.run_host("commands")

    def test_scalar_copies_and_reference_handoffs(self):
        self.run_host("scalar")


if __name__ == "__main__":
    unittest.main()
