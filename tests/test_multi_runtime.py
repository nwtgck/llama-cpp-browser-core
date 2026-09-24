# Synthetic Wasm headers below test packaging ONLY, never inference/build success.
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('multi_runtime_package', ROOT / 'scripts/package_runtime.py')
package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)
SOURCE = 'a' * 40

def write_manifest(root, data):
    data['files'] = [{'path': p.relative_to(root).as_posix(), 'bytes': p.stat().st_size,
                      'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                     for p in sorted(root.rglob('*')) if p.is_file() and p != root / 'manifest.json']
    (root / 'manifest.json').write_text(json.dumps(data))

class MultiRuntime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.inputs = self.root / 'inputs'; self.out = self.root / 'out'
        for runtime in package.RUNTIMES: self.fixture(runtime)
    def tearDown(self): self.tmp.cleanup()
    def fixture(self, runtime):
        root = self.inputs / runtime; root.mkdir(parents=True)
        validator = package.runtime_module(runtime)
        def put(relative, value):
            p = root / relative; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(value if isinstance(value, bytes) else value.encode())
        put('LICENSE', 'fixture notice')
        put('package.json', json.dumps({'name': 'llama-cpp-browser-core' if runtime == 'llama-cpp' else validator.NAME,
                                      'version': '0.1.0', 'type': 'module', 'files': ['profiles/', 'licenses/', 'api/', 'examples/', 'LICENSE', 'manifest.json']}))
        manifest = {'sourceCommit': SOURCE, 'profiles': {}}
        if runtime == 'llama-cpp':
            manifest.update(formatVersion=2, llamaCommit='b' * 40)
            for relative in validator.EMBEDDED_NOTICE_FILES: put('licenses/embedded/' + relative + '.txt', 'fixture embedded notice')
            for name in validator.EXAMPLE_RUNTIME_FILES: put('examples/runtime/' + name, 'fixture')
            profiles = {'cpu-wasm32': {}}
        else:
            manifest.update(formatVersion=1, abiVersion=1, runtime=runtime, upstreams={'fixture': 'b' * 40}, experimental=True)
            for name in ['stable-diffusion/LICENSE', 'ggml/LICENSE', *['embedded/' + p + '.txt' for p in ['json.hpp', 'stb_image.h', 'stb_image_resize.h', 'stb_image_write.h']]]: put('licenses/' + name, 'fixture notice')
            profiles = validator.PROFILES
        for profile, config in profiles.items():
            variants = {}
            for variant, settings in validator.VARIANTS.items():
                prefix = f'profiles/{profile}/{variant}/'
                put(prefix + 'core.wasm', b'\0asm\1\0\0\0')
                put(prefix + 'core.mjs', 'export default () => "fixture";')
                put(prefix + 'core.d.ts', 'export default function create(): string;')
                data = {'profile': profile, 'variant': variant, 'sourceCommit': SOURCE, 'sourceDirty': False,
                        'variantConfiguration': settings, 'validation': {'compiled': True, 'fixtureOnly': True, 'realModelInference': False}}
                if runtime == 'llama-cpp': data['llamaCommit'] = manifest['llamaCommit']
                else: data.update(upstreams=manifest['upstreams'], configuration=config, patches={'patches': [{'fixtureOnly': True}]})
                variants[variant] = data
            manifest['profiles'][profile] = {'variants': variants}
        write_manifest(root, manifest)
    def test_complete_package_has_namespaces_and_resolvable_legacy_alias(self):
        package.assemble(self.inputs, self.out)
        result = package.validate(self.out)
        self.assertEqual(result['formatVersion'], 3)
        self.assertEqual(set(result['runtimes']), set(package.RUNTIMES))
        self.assertFalse((self.out / 'profiles').exists())
        subprocess.run(['node', '--input-type=module', '-e', """
          const old = await import('llama-cpp-browser-core/profiles/cpu-wasm32/browser/core.mjs');
          const named = await import('llama-cpp-browser-core/llama-cpp/profiles/cpu-wasm32/browser/core.mjs');
          const image = await import('llama-cpp-browser-core/stable-diffusion-cpp/profiles/webgpu-wasm32-asyncify/browser/core.mjs');
          if (old.default !== named.default || image.default() !== 'fixture') throw Error('Wrong export mapping');
        """], cwd=self.out, check=True)
    def test_consumer_report_rebases_paths_and_keeps_image_provenance_bounded(self):
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        spec = importlib.util.spec_from_file_location('multi_consumer', ROOT / 'scripts/consumer_metadata.py')
        reporter = importlib.util.module_from_spec(spec); spec.loader.exec_module(reporter)
        package.assemble(self.inputs, self.out)
        # No network, npm lock generation, or real upstream build is claimed.
        data = reporter.metadata(self.out, 'example/lcore', 'd' * 40, {'specifier': 'github:example/lcore#' + 'd' * 40}, {'baseCommit': 'b' * 40})
        self.assertTrue(data['retrieval']['sourceRawBase'].endswith('/llama-cpp/'))
        self.assertEqual(data['runtime']['manifestFormatVersion'], 3)
        self.assertTrue(data['browserProfiles']['cpu-wasm32']['wasm']['path'].startswith('llama-cpp/profiles/'))
        sd = data['stableDiffusion']
        self.assertEqual(sd['manifest']['path'], 'stable-diffusion-cpp/manifest.json')
        self.assertIn('#' + 'd' * 40, sd['installSeparatelyForNaidan'])
        for profile in sd['profiles'].values():
            for item in profile['variants'].values():
                self.assertEqual(set(item), {'validation', 'sourceCommit'})
                self.assertFalse(item['validation']['realModelInference'])
        text = reporter.legacy.write_report(self.root / 'report', data, '123', '1')
        self.assertLess(len(text.encode('utf8')), 55000)

    def test_mixed_sources_cannot_assemble(self):
        root = self.inputs / 'stable-diffusion-cpp'
        m = json.loads((root / 'manifest.json').read_text()); m['sourceCommit'] = 'c' * 40
        for info in m['profiles'].values():
            for data in info['variants'].values(): data['sourceCommit'] = 'c' * 40
        write_manifest(root, m)
        with self.assertRaisesRegex(ValueError, 'mix'): package.assemble(self.inputs, self.out)
    def test_tampering_is_detected(self):
        package.assemble(self.inputs, self.out)
        (self.out / 'stable-diffusion-cpp/profiles/webgpu-wasm32-asyncify/browser/core.wasm').write_bytes(b'invalid')
        with self.assertRaisesRegex(ValueError, 'hash/size'): package.validate(self.out)
    def test_missing_variant_is_not_publishable(self):
        root = self.inputs / 'stable-diffusion-cpp'; m = json.loads((root / 'manifest.json').read_text())
        del m['profiles']['webgpu-wasm32-jspi']['variants']['test']; write_manifest(root, m)
        with self.assertRaisesRegex(ValueError, 'variants'): package.assemble(self.inputs, self.out)
    def test_dirty_image_source_blocks_entire_publication(self):
        root = self.inputs / 'stable-diffusion-cpp'; m = json.loads((root / 'manifest.json').read_text())
        m['profiles']['webgpu-wasm32-jspi']['variants']['test']['sourceDirty'] = True; write_manifest(root, m)
        with self.assertRaisesRegex(ValueError, 'dirty'): package.assemble(self.inputs, self.out)
    def test_assembly_requires_both_runtimes(self):
        import shutil
        shutil.rmtree(self.inputs / 'stable-diffusion-cpp')
        with self.assertRaises(FileNotFoundError): package.assemble(self.inputs, self.out)

if __name__ == '__main__': unittest.main()
