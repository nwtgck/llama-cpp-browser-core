import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sdb_prepare', ROOT / 'scripts/prepare_upstream.py')
prepare_module = importlib.util.module_from_spec(spec); spec.loader.exec_module(prepare_module)

class Preparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources = {name: self.root / name for name in ('stable-diffusion', 'ggml')}
        for source in self.sources.values(): source.mkdir()
        (self.sources['stable-diffusion'] / 'a.txt').write_text('before\n')
        self.patches = self.root / 'patches'; self.patches.mkdir()
        (self.patches / 'fix.patch').write_text('diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-before\n+after\n')
        self.inventory = {'formatVersion': 1, 'patches': [{'file': 'fix.patch', 'target': 'stable-diffusion', 'reason': 'test fixture', 'files': [{'path': 'a.txt', 'beforeSha256': hashlib.sha256(b'before\n').hexdigest(), 'afterSha256': hashlib.sha256(b'after\n').hexdigest()}]}]}
        self.write_inventory()
    def tearDown(self): self.tmp.cleanup()
    def write_inventory(self): (self.patches / 'series.json').write_text(json.dumps(self.inventory))
    def apply(self): return prepare_module.prepare(self.sources, self.root / 'prepared', self.patches)
    def test_prepares_exact_copy_without_mutating_vendor(self):
        result = self.apply()
        self.assertFalse(result['vendorCheckoutModified'])
        self.assertEqual((self.sources['stable-diffusion'] / 'a.txt').read_text(), 'before\n')
        self.assertEqual((self.root / 'prepared/stable-diffusion/a.txt').read_text(), 'after\n')
        self.assertEqual(result['patches'][0]['sha256'], prepare_module.digest(self.patches / 'fix.patch'))
    def test_rejects_changed_input_without_partial_destination(self):
        (self.sources['stable-diffusion'] / 'a.txt').write_text('not approved\n')
        with self.assertRaisesRegex(ValueError, 'Unreviewed'): self.apply()
        self.assertFalse((self.root / 'prepared').exists())
    def test_rejects_changed_expected_output(self):
        self.inventory['patches'][0]['files'][0]['afterSha256'] = '0' * 64; self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'Unexpected'): self.apply()
    def test_rejects_unknown_target(self):
        self.inventory['patches'][0]['target'] = '../outside'; self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'Unknown'): self.apply()
    def test_rejects_unlisted_edits(self):
        (self.sources['stable-diffusion'] / 'b.txt').write_text('before\n')
        with (self.patches / 'fix.patch').open('a') as stream: stream.write('diff --git a/b.txt b/b.txt\n--- a/b.txt\n+++ b/b.txt\n@@ -1 +1 @@\n-before\n+after\n')
        with self.assertRaisesRegex(ValueError, 'inventory'): self.apply()
    def test_rejects_existing_destination(self):
        (self.root / 'prepared').mkdir()
        with self.assertRaisesRegex(ValueError, 'already exist'): self.apply()
    def test_rejects_destination_inside_source(self):
        with self.assertRaisesRegex(ValueError, 'inside'): prepare_module.prepare(self.sources, self.sources['ggml'] / 'build', self.patches)
    def test_rejects_linked_input(self):
        original = self.sources['stable-diffusion'] / 'a.txt'; original.unlink()
        (self.root / 'outside.txt').write_text('before\n'); original.symlink_to(self.root / 'outside.txt')
        with self.assertRaises(ValueError): self.apply()
if __name__ == '__main__': unittest.main()
