"""Every ```lsf block in the documentation must be a complete program that compiles.

Needs build/lsf_cli: run tests/run.sh first. Run: python3 -I -m unittest discover -s tests
"""
import glob
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "build", "lsf_cli")
FENCE = re.compile(r"^```lsf[ \t]*\n(.*?)^```[ \t]*$", re.S | re.M)


def doc_files():
    files = [os.path.join(ROOT, "README.md"), os.path.join(ROOT, "AGENTS.md"), os.path.join(ROOT, "examples", "README.md")]
    return files + sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")))


class DocsCompile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CLI):
            raise unittest.SkipTest("build/lsf_cli missing: run tests/run.sh first")

    def test_blocks_found(self):
        total = sum(len(FENCE.findall(open(f, encoding="utf-8").read())) for f in doc_files())
        self.assertGreaterEqual(total, 10, "the extractor found suspiciously few lsf blocks")

    def test_every_block_compiles(self):
        failures = []
        for path in doc_files():
            text = open(path, encoding="utf-8").read()
            for k, src in enumerate(FENCE.findall(text), 1):
                with tempfile.NamedTemporaryFile("w", suffix=".lsf", delete=False) as f:
                    f.write(src)
                try:
                    # -f 3 so formulas that read the previous frame run through it as well
                    r = subprocess.run([CLI, f.name, "-n", "16", "-f", "3"], capture_output=True, text=True)
                finally:
                    os.unlink(f.name)
                if r.returncode != 0:
                    failures.append("%s block %d: %s\n%s" % (os.path.relpath(path, ROOT), k, r.stderr.strip(), src))
        self.assertEqual(failures, [], "\n\n".join(failures))

    def test_examples_listed_in_readme(self):
        listed = open(os.path.join(ROOT, "examples", "README.md"), encoding="utf-8").read()
        for path in sorted(glob.glob(os.path.join(ROOT, "examples", "*.lsf"))):
            self.assertIn(os.path.basename(path), listed, "%s is not described in examples/README.md" % os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
