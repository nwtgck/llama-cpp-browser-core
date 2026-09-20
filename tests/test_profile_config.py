"""Validate profile selection with mocked subprocesses; never configure or build."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build


class ProfileConfiguration(unittest.TestCase):
    def test_ci_builds_every_packaged_profile(self):
        profiles = json.loads((ROOT / 'config/profiles.json').read_text())
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        matrix = workflow.split('        profile:\n', 1)[1].split('    steps:', 1)[0]
        self.assertEqual(set(re.findall(r'^          - ([a-z0-9-]+)$', matrix, re.MULTILINE)),
                         set(profiles))

    def test_each_profile_passes_its_suspension_and_memory_configuration(self):
        profiles = json.loads((ROOT / 'config/profiles.json').read_text())
        expected = {
            'cpu-wasm32': ('OFF', 'OFF', 'OFF', 'OFF', '4294967296'),
            'cpu-wasm64': ('ON', 'OFF', 'OFF', 'OFF', '17179869184'),
            'webgpu-wasm32-asyncify': ('OFF', 'ON', 'OFF', 'ON', '4294967296'),
            'webgpu-wasm64-jspi': ('ON', 'ON', 'ON', 'OFF', '17179869184'),
        }
        self.assertEqual(set(profiles), set(expected))
        toolchain = json.loads((ROOT / 'config/toolchain.json').read_text())
        with tempfile.TemporaryDirectory(prefix='lcb-profile-test-') as temporary:
            root = Path(temporary)
            (root / 'config').mkdir()
            (root / 'config/profiles.json').write_text(json.dumps(profiles))
            (root / 'config/toolchain.json').write_text(json.dumps(toolchain))
            (root / 'vendor/llama.cpp/include').mkdir(parents=True)
            (root / 'vendor/llama.cpp/include/llama.h').touch()
            (root / '.tools/emdawnwebgpu_pkg').mkdir(parents=True)
            (root / '.tools/emdawnwebgpu_pkg/emdawnwebgpu.port.py').touch()
            for profile, values in expected.items():
                with self.subTest(profile=profile), \
                     patch.object(build, 'ROOT', root), \
                     patch.object(build, 'source_status', return_value=[]), \
                     patch.object(build.shutil, 'which', return_value='/test-toolchain/emcc'), \
                     patch.object(build, 'verify_asyncify_bigint_patch') as verify_patch, \
                     patch.object(build, 'output', side_effect=[
                         toolchain['llamaCommit'], f'emcc {toolchain["emsdkVersion"]}', 'a' * 40,
                     ]), \
                     patch.object(build.subprocess, 'run') as run, \
                     patch.object(sys, 'argv', ['build.py', '--profile', profile]), \
                     patch('builtins.print'):
                    build.main()
                    command = run.call_args_list[0].args[0]
                    names = ('MEMORY64', 'WEBGPU', 'JSPI', 'ASYNCIFY', 'MAXIMUM_MEMORY')
                    for name, value in zip(names, values):
                        self.assertIn(f'-DLCB_{name}={value}', command)
                    self.assertEqual(any(arg.startswith('-DEMDAWNWEBGPU_DIR=') for arg in command),
                                     profiles[profile]['webgpu'])
                    provenance = json.loads((root / 'build' / profile / 'provenance.json').read_text())
                    self.assertEqual(provenance['configuration'], profiles[profile])
                    if profiles[profile]['asyncify']:
                        verify_patch.assert_called_once_with(Path('/test-toolchain'), toolchain['emscriptenAsyncifyBigIntPatch'])
                    else:
                        verify_patch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
