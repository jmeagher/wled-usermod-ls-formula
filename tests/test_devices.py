"""Every device formula under board_setup/devices must compile and fit the default limits.

Needs build/lsf_cli: run tests/run.sh first. Run: python3 -I -m unittest discover -s tests
"""
import glob
import os
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "build", "lsf_cli")
FORMULAS = sorted(glob.glob(os.path.join(ROOT, "board_setup", "devices", "*", "*.lsf")))


class DeviceFormulas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def test_there_are_formulas(self):
        self.assertTrue(FORMULAS)

    def test_compile_and_render(self):
        for path in FORMULAS:
            with self.subTest(os.path.relpath(path, ROOT)):
                r = subprocess.run([CLI, path, "-n", "50", "-t", "12345", "-f", "1"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(r.stdout.split()), 50)

    def test_source_fits_the_default_limit(self):
        for path in FORMULAS:
            with self.subTest(os.path.relpath(path, ROOT)):
                self.assertLessEqual(os.path.getsize(path), 1024)


if __name__ == "__main__":
    unittest.main()
