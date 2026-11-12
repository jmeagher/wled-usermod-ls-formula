"""The formulas board_setup/devices/*/apply.sh installs must exist, compile and fit the default limits.

Needs build/lsf_cli: run tests/run.sh first. Run: python3 -I -m unittest discover -s tests
"""
import glob
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "build", "lsf_cli")
ENTRY = re.compile(r'^\s*"(\d+)\|([\w.-]+\.lsf)\|([^|]+)\|(\d+)"\s*$', re.M)   # "slot|file|preset name|preset id"


def installed():
    out = []
    for script in sorted(glob.glob(os.path.join(ROOT, "board_setup", "devices", "*", "apply.sh"))):
        for slot, fname, name, preset in ENTRY.findall(open(script, encoding="utf-8").read()):
            out.append((os.path.relpath(script, ROOT), int(slot), os.path.join(ROOT, "examples", fname), name, int(preset)))
    return out


class DeviceFormulas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def test_scripts_list_formulas(self):
        self.assertTrue(installed())

    def test_formulas_exist_compile_and_fit(self):
        for script, slot, path, name, preset in installed():
            with self.subTest("%s: %s" % (script, os.path.basename(path))):
                self.assertTrue(os.path.exists(path), "%s installs %s, which is not in examples/" % (script, path))
                self.assertLessEqual(os.path.getsize(path), 1024)
                r = subprocess.run([CLI, path, "-n", "50", "-t", "12345", "-f", "1"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(len(r.stdout.split()), 50)

    def test_slots_and_presets_are_unique(self):
        for script in {s for s, *_ in installed()}:
            rows = [r for r in installed() if r[0] == script]
            self.assertEqual(len({r[1] for r in rows}), len(rows), "duplicate slot in " + script)
            self.assertEqual(len({r[4] for r in rows}), len(rows), "duplicate preset id in " + script)


if __name__ == "__main__":
    unittest.main()
