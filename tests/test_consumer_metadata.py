"""Validate compact reporting data independently of real runtime compilation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import consumer_metadata as consumer
import upstream_provenance as provenance

A, B, C = 'a' * 40, 'b' * 40, 'c' * 40
REPO = 'example/core'
SPEC = f'github:{REPO}#{C}'
NAME = 'llama-cpp-browser-core'
ENTRY = {'version': '0.1.0', 'resolved': f'git+ssh://git@github.com/{REPO}.git#{C}',
         'integrity': 'sha512-fixture-not-a-real-package-hash', 'license': 'MIT'}


def fixture_lock():
    return {'lockfileVersion': 3, 'packages': {'': {'dependencies': {NAME: SPEC}}, 'node_modules/' + NAME: copy.deepcopy(ENTRY)}}


def fixture_package(root):
    manifest = {'formatVersion': 2, 'sourceCommit': A, 'llamaCommit': B, 'profiles': {}, 'files': []}
    def file(path, content):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        manifest['files'].append({'path': path, **provenance.file_identity(target)})
    profiles = ['cpu-wasm32', 'cpu-wasm64', 'webgpu-wasm32-asyncify', 'webgpu-wasm32-jspi', 'webgpu-wasm64-jspi']
    for name in profiles:
        manifest['profiles'][name] = {'variants': {}}
        for variant in ['browser', 'test']:
            for ext in ['mjs', 'wasm', 'd.ts']:
                file(f'profiles/{name}/{variant}/core.{ext}', f'Fixture: {name}/{variant}/{ext}\n'.encode())
            manifest['profiles'][name]['variants'][variant] = {
                'validation': {'compiled': True, 'browserSmoke': True, 'realModelInference': False},
            }
    file('api/schema.json', b'{}\n')
    file('api/functions.d.ts', b'// Fixture\n')
    file('examples/runtime/index.mjs', b'// Fixture\n')
    file('examples/runtime/README.md', b'Test documentation\n')
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


class LockMetadata(unittest.TestCase):
    def test_lock_entry_is_preserved_including_unknown_npm_fields(self):
        lock = fixture_lock()
        lock['packages']['node_modules/' + NAME]['someFutureNpmField'] = 'value'
        result = consumer.extract_lock(lock, SPEC, C, '0.1.0')
        self.assertEqual(result['packageEntry'], lock['packages']['node_modules/' + NAME])

    def test_absent_integrity_is_not_invented(self):
        lock = fixture_lock()
        del lock['packages']['node_modules/' + NAME]['integrity']
        self.assertNotIn('integrity', consumer.extract_lock(lock, SPEC, C, '0.1.0')['packageEntry'])

    def test_wrong_commit_version_specifier_or_lock_schema_is_rejected(self):
        changes = [lambda x: x.update(lockfileVersion=2),
                   lambda x: x['packages']['']['dependencies'].update({NAME: 'latest'}),
                   lambda x: x['packages']['node_modules/' + NAME].update(version='0.2.0'),
                   lambda x: x['packages']['node_modules/' + NAME].update(resolved='git+https://example/x#' + A)]
        for change in changes:
            value = fixture_lock(); change(value)
            with self.assertRaises(ValueError):
                consumer.extract_lock(value, SPEC, C, '0.1.0')

    def test_transitive_dependencies_or_install_hooks_are_rejected(self):
        for key, value in [('dependencies', {'another': '1'}), ('optionalDependencies', {'another': '1'}),
                           ('peerDependencies', {'another': '1'}), ('hasInstallScript', True)]:
            lock = fixture_lock(); lock['packages']['node_modules/' + NAME][key] = value
            with self.assertRaises(ValueError):
                consumer.extract_lock(lock, SPEC, C, '0.1.0')
        lock = fixture_lock(); lock['packages']['node_modules/another'] = {}
        with self.assertRaises(ValueError):
            consumer.extract_lock(lock, SPEC, C, '0.1.0')

    def test_ci_resolution_uses_npm_with_no_scripts_and_an_isolated_lock(self):
        def npm(command, *, cwd, env, **kwargs):
            self.assertIn('--package-lock-only', command)
            self.assertIn('--ignore-scripts', command)
            self.assertIn('--lockfile-version=3', command)
            self.assertEqual(json.loads((cwd / 'package.json').read_text())['dependencies'], {NAME: SPEC})
            self.assertTrue(str(env['NPM_CONFIG_CACHE']).startswith(str(cwd)))
            (cwd / 'package-lock.json').write_text(json.dumps(fixture_lock()))
        with patch.object(consumer.subprocess, 'run', side_effect=npm), \
             patch.object(consumer.subprocess, 'check_output', side_effect=['v22.16.0\n', '10.9.2\n']):
            result = consumer.generate_lock(REPO, C, '0.1.0')
        self.assertEqual(result['packageEntry'], ENTRY)
        self.assertEqual(result['npmVersion'], '10.9.2')


class ReportMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = fixture_package(self.root)
        lock = consumer.extract_lock(fixture_lock(), SPEC, C, '0.1.0')
        lock['specifier'] = SPEC
        self.data = consumer.metadata(self.root, REPO, C, lock, {'baseCommit': B, 'sourceOverlays': []})

    def test_actual_browser_profiles_and_files_are_used_without_hash_duplication(self):
        self.assertEqual(len(self.data['browserProfiles']), 5)
        for name, files in self.data['browserProfiles'].items():
            for kind in ['mjs', 'wasm', 'types']:
                entry = files[kind]
                self.assertEqual(entry['sha256'], provenance.file_identity(self.root / entry['path'])['sha256'])
                self.assertIn('/browser/', entry['path'])
                self.assertNotIn('/test/', entry['path'])
        paths = [entry['path'] for entry in self.data['interfaceFiles']]
        self.assertIn('api/schema.json', paths)
        self.assertIn('examples/runtime/index.mjs', paths)
        self.assertNotIn('examples/runtime/README.md', paths)

    def test_manifest_itself_is_pinned_outside_the_runtime_package(self):
        self.assertEqual(self.data['retrieval']['manifest']['sha256'], provenance.file_identity(self.root / 'manifest.json')['sha256'])
        for name in ['artifactArchive', 'artifactRawBase']:
            self.assertIn(C, self.data['retrieval'][name])
        for name in ['sourceArchive', 'sourceRawBase']:
            self.assertIn(A, self.data['retrieval'][name])
        self.assertIn(B, self.data['retrieval']['upstreamRawBase'])

    def test_overlay_base_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            consumer.metadata(self.root, REPO, C, {}, {'baseCommit': A})

    def test_duplicate_manifest_file_is_rejected(self):
        self.manifest['files'].append(self.manifest['files'][0])
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            consumer.metadata(self.root, REPO, C, {}, {'baseCommit': B})

    def test_one_yaml_contains_all_pin_locations_and_search_hints(self):
        text = consumer.render_yaml(self.data)
        for term in ['package.json', 'package-lock.json', 'coreHashes', 'transformBrowserCore',
                     'Reviewed browser variant artifact commit', 'standaloneWasm', 'manifestSchema',
                     'profileSchema', 'upstreamDivergences', 'knownLocations', 'searchHints',
                     'build/llama-cpp-browser-core.test.ts', 'Brotli capability-probe']:
            self.assertIn(term, text)
        self.assertIn('may have changed', text)
        self.assertNotIn('Policy exception', text)
        self.assertNotIn('must not normally', text)
        self.assertNotIn('ChatGPT', text)
        self.assertNotIn('currentValue', text)
        self.assertLess(len(text), 22000)

    def test_report_is_english_and_descriptive_not_an_assistant_prompt(self):
        text = consumer.render_yaml(self.data)
        self.assertTrue(text.isascii())
        for command in ['Do not ', 'Update this', 'Replace this', 'You must', 'Search Naidan']:
            self.assertNotIn(command, text)

    def test_report_and_envelope_match_and_have_one_collapsed_yaml_block(self):
        output = self.root / 'report'
        text = consumer.write_report(output, self.data, '123', '2')
        self.assertEqual(text, (output / 'consumer-update.md').read_text())
        self.assertEqual(text.count('```yaml'), 1)
        self.assertIn('<details>', text)
        envelope = json.loads((output / 'report.json').read_text())
        self.assertEqual(envelope['runId'], 123)
        self.assertEqual(envelope['runAttempt'], 2)
        self.assertEqual(envelope['markdownSha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(set(path.name for path in output.iterdir()), {'consumer-update.md', 'consumer-update.yaml', 'report.json'})

    def test_oversized_report_fails_instead_of_truncating(self):
        self.data['huge'] = 'x' * 55000
        with self.assertRaisesRegex(ValueError, 'too large'):
            consumer.write_report(self.root / 'report', self.data, '1', '1')

    def test_yaml_ambiguous_scalars_are_quoted(self):
        for value in ['on', 'OFF', 'null', 'yes', '2026-09-22', '1234', '123e9', 'a: b', 'x\ny', 'value # comment', 'abc:']:
            self.assertEqual(consumer.scalar(value), json.dumps(value))
        self.assertEqual(consumer.scalar('profileSchema'), 'profileSchema')
        self.assertEqual(consumer.scalar(True), 'true')
        self.assertEqual(consumer.scalar(False), 'false')
        self.assertEqual(consumer.scalar(0), '0')
        self.assertEqual(consumer.scalar(None), 'null')

    def test_yaml_round_trip_when_parser_available(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('Optional local YAML round-trip check; generator has no third-party dependencies')
        self.assertEqual(yaml.safe_load(consumer.render_yaml(self.data)), self.data)


class OverlayProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vendor = self.root / 'vendor/llama.cpp'
        (self.vendor / 'tools/mtmd').mkdir(parents=True)
        (self.vendor / 'tools/mtmd/clip.cpp').write_text('before\noriginal\nafter\n')
        toolchain = {'llamaCommit': B, 'emscriptenRelease': A,
                     'emscriptenAsyncifyBigIntPatch': {'sourceSha256': '1' * 64, 'patchedSha256': '2' * 64}}
        for name in ['config', 'patches', 'scripts', 'cmake', 'bridge', 'docs']:
            (self.root / name).mkdir()
        (self.root / 'config/toolchain.json').write_text(json.dumps(toolchain))
        (self.root / 'patches/mtmd-webgpu-bf16.patch').write_text('--- a/clip.cpp\n+++ b/clip.cpp\n@@ -1,3 +1,3 @@\n before\n-original\n+patched\n after\n')
        for path in ['scripts/prepare_mtmd.py', 'cmake/MtmdOverlay.cmake', 'bridge/mtmd-bf16.h',
                     'docs/webgpu-bf16-projector.md', 'scripts/patch_emscripten.py']:
            (self.root / path).write_text('Provenance fixture: ' + path + '\n')
        self.manifest = {'sourceCommit': A, 'llamaCommit': B, 'profiles': {}}
        for name, enabled in [('cpu-wasm32', False), ('webgpu-wasm64-jspi', True)]:
            self.manifest['profiles'][name] = {'variants': {variant: {
                'toolchain': toolchain, 'cmakeCommand': ['cmake', '-DLCB_WEBGPU_BF16_PROJECTOR=' + ('ON' if enabled else 'OFF')],
            } for variant in ['browser', 'test']}}
        self.mock_git = patch.object(provenance, 'git', side_effect=lambda *args, cwd: subprocess.CompletedProcess(
            args, 0, stdout=(A if cwd == self.root else B) + '\n'))
        self.mock_git.start()
        self.addCleanup(self.mock_git.stop)

    def test_overlay_hashes_describe_pristine_and_actual_patched_copy(self):
        result = provenance.collect(self.root, self.manifest)
        overlay = result['sourceOverlays'][0]
        self.assertFalse(result['vendorCheckoutModified'])
        self.assertEqual(overlay['compiledCopy']['sha256'], hashlib.sha256(b'before\npatched\nafter\n').hexdigest())
        self.assertEqual(overlay['upstreamSource']['sha256'], hashlib.sha256(b'before\noriginal\nafter\n').hexdigest())
        self.assertEqual(overlay['application']['enabledProfileVariants'], ['webgpu-wasm64-jspi/browser', 'webgpu-wasm64-jspi/test'])
        self.assertEqual((self.vendor / 'tools/mtmd/clip.cpp').read_text(), 'before\noriginal\nafter\n')
        self.assertIn('toolchainDivergences', result)

    def test_unknown_patch_files_are_not_silently_omitted(self):
        (self.root / 'patches/another.patch').write_text('Another fixture patch\n')
        report = provenance.collect(self.root, self.manifest)
        self.assertIn('patches/another.patch', report['otherPatchFiles'])
        self.assertEqual(report['otherPatchFiles']['patches/another.patch']['application'], 'not classified by this report')

    def test_unknown_build_activation_fails(self):
        self.manifest['profiles']['cpu-wasm32']['variants']['browser']['cmakeCommand'] = ['cmake']
        with self.assertRaisesRegex(ValueError, 'activation'):
            provenance.collect(self.root, self.manifest)

    def test_patch_conflict_and_source_commit_mismatch_fail(self):
        self.manifest['sourceCommit'] = C
        with self.assertRaisesRegex(ValueError, 'source commit'):
            provenance.collect(self.root, self.manifest)
        self.manifest['sourceCommit'] = A
        (self.vendor / 'tools/mtmd/clip.cpp').write_text('changed upstream\n')
        with self.assertRaises(subprocess.CalledProcessError):
            provenance.collect(self.root, self.manifest)

    def test_symlinked_source_files_are_rejected(self):
        source = self.vendor / 'tools/mtmd/clip.cpp'
        copied = self.root / 'elsewhere'
        source.rename(copied)
        source.symlink_to(copied)
        with self.assertRaisesRegex(ValueError, 'linked'):
            provenance.collect(self.root, self.manifest)


if __name__ == '__main__':
    unittest.main()
