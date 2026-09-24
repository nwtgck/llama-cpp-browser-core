"""Content verification is mandatory on cache hits as well as downloads."""
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT/'scripts'))
from setup_toolchain import prepare_dawn


class DawnArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.tools = Path(self.tmp.name)
        self.config = {'dawnTag': 'fixture', 'dawnSha256': ''}
        self.archive = self.tools/'downloads/emdawnwebgpu_pkg-fixture.zip'
        self.archive.parent.mkdir()

    def tearDown(self): self.tmp.cleanup()

    def archive_bytes(self, name='emdawnwebgpu_pkg/emdawnwebgpu.port.py', symlink=False):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z:
            info = zipfile.ZipInfo(name)
            if symlink: info.external_attr = 0o120777 << 16
            z.writestr(info, 'fixture data, not a real port')
        value = out.getvalue(); self.config['dawnSha256'] = hashlib.sha256(value).hexdigest()
        return value

    def test_cached_download_is_verified_and_port_reconstructed_without_network(self):
        self.archive.write_bytes(self.archive_bytes())
        stale = self.tools/'emdawnwebgpu_pkg/stale.py'; stale.parent.mkdir(); stale.write_text('stale')
        with patch('setup_toolchain.urllib.request.urlopen', side_effect=AssertionError('No network on hit')):
            prepare_dawn(self.config, self.tools)
        self.assertFalse(stale.exists())
        self.assertTrue((stale.parent/'emdawnwebgpu.port.py').is_file())

    def test_corrupt_cached_archive_fails_before_existing_port_is_removed(self):
        data = self.archive_bytes(); self.archive.write_bytes(data + b'changed')
        old = self.tools/'emdawnwebgpu_pkg/old'; old.parent.mkdir(); old.write_text('preserve until verification')
        with patch('setup_toolchain.urllib.request.urlopen', side_effect=AssertionError('No silent replacement')):
            with self.assertRaisesRegex(RuntimeError, 'checksum'): prepare_dawn(self.config, self.tools)
        self.assertTrue(old.is_file())

    def test_download_miss_is_also_verified(self):
        data = self.archive_bytes()
        with patch('setup_toolchain.urllib.request.urlopen', return_value=io.BytesIO(data)) as fetch:
            prepare_dawn(self.config, self.tools)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.archive.read_bytes(), data)
        self.assertFalse(self.archive.with_suffix('.download').exists())

    def test_bad_download_hash_does_not_extract(self):
        self.archive_bytes()
        with patch('setup_toolchain.urllib.request.urlopen', return_value=io.BytesIO(b'bad download')):
            with self.assertRaisesRegex(RuntimeError, 'checksum'): prepare_dawn(self.config, self.tools)
        self.assertFalse((self.tools/'emdawnwebgpu_pkg').exists())

    def test_archive_path_traversal_and_symlinks_are_rejected(self):
        for name, linked in [('../escape', False), ('/tmp/escape', False), ('emdawnwebgpu_pkg/link', True)]:
            with self.subTest(name=name):
                self.archive.write_bytes(self.archive_bytes(name, linked))
                with self.assertRaisesRegex(RuntimeError, 'Unsafe'): prepare_dawn(self.config, self.tools)

    def test_cached_archive_and_extracted_port_cannot_be_symlinks(self):
        target = self.tools/'target'; target.write_bytes(self.archive_bytes())
        self.archive.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'linked'): prepare_dawn(self.config, self.tools)
        self.archive.unlink(); self.archive.write_bytes(target.read_bytes())
        (self.tools/'emdawnwebgpu_pkg').symlink_to(self.tools, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'linked'): prepare_dawn(self.config, self.tools)


if __name__ == '__main__': unittest.main()
