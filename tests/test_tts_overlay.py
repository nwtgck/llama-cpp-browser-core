"""Overlay and native graph checks; no trained-model/GPU quality claim is made."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_tts import FILES, prepare_tts
from prepare_mtmd import prepare


class TtsOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='lcb-tts-test-')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.source = Path(os.environ.get('LCB_TEST_LLAMA_SOURCE', ROOT / 'vendor/llama.cpp'))

    def overlay(self):
        if not (self.source / FILES[0]).is_file():
            self.skipTest('Initialize the pinned submodule or set LCB_TEST_LLAMA_SOURCE')
        return prepare_tts(self.source, self.work / 'overlay', capture_output=True)

    def test_source_is_immutable_and_copy_is_idempotent(self):
        overlay = self.overlay()
        before = {name: (self.source / name).read_bytes() for name in FILES}
        stamps = {name: (overlay / name).stat().st_mtime_ns for name in FILES}
        prepare_tts(self.source, overlay, capture_output=True)
        self.assertEqual(before, {name: (self.source / name).read_bytes() for name in FILES})
        self.assertEqual(stamps, {name: (overlay / name).stat().st_mtime_ns for name in FILES})
        for path in (self.source / 'tools/mtmd').rglob('*'):
            if path.is_file() and path.suffix in ('.cpp', '.h', '.hpp'):
                self.assertTrue((overlay / path.relative_to(self.source)).is_file())
        self.assertIn('mtmd_helper_gen_audio_supports_language_auto', (overlay / FILES[-1]).read_text())

    def test_bad_patch_never_publishes_partial_output(self):
        overlay = self.overlay()
        before = {name: (overlay / name).read_bytes() for name in FILES}
        invalid = self.work / 'invalid.patch'
        invalid.write_text('not a patch\n')
        with self.assertRaises(subprocess.CalledProcessError):
            prepare_tts(self.source, overlay, invalid, capture_output=True)
        self.assertEqual(before, {name: (overlay / name).read_bytes() for name in FILES})

    def test_short_contexts_tolerate_blank_line_formatting_but_not_ambiguity(self):
        self.overlay()
        copy = self.work / 'formatting'
        for name in FILES:
            path = copy / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(line for line in (self.source / name).read_text().splitlines(keepends=True) if line.strip()))
        prepare_tts(copy, self.work / 'formatting-out', capture_output=True)
        source = copy / 'tools/mtmd/mtmd-helper.h'
        source.write_text(source.read_text() + '\nMTMD_API void mtmd_helper_gen_audio_free(mtmd_helper_gen_audio * ctx);\n')
        with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
            prepare_tts(copy, self.work / 'ambiguous-out', capture_output=True)

    def test_nested_source_destinations_are_rejected(self):
        for source, output in [(self.work, self.work), (self.work, self.work / 'inside'), (self.work / 'inside', self.work)]:
            with self.subTest(source=source, output=output), self.assertRaises(ValueError):
                prepare_tts(source, output)

    def test_vision_overlay_keeps_decoder_changes(self):
        overlay = self.overlay()
        target = prepare(overlay, self.work / 'vision', ROOT / 'patches/mtmd-webgpu-bf16.patch')
        text = target.read_text()
        self.assertIn('params->codes->size() /', text)
        self.assertIn('n_frames * n_codes', text)
        self.assertNotEqual(target.read_bytes(), (overlay / FILES[0]).read_bytes())

    def native(self):
        overlay = self.overlay()
        build = Path(os.environ.get('LCB_TEST_NATIVE_BUILD', ROOT / 'build/native'))
        compiler = os.environ.get('LCB_TEST_CXX') or shutil.which('g++') or shutil.which('clang++')
        if not compiler or not (build / 'libcore.so').is_file():
            self.skipTest('Set LCB_TEST_NATIVE_BUILD to a native build of this patched core')
        return overlay / 'tools/mtmd', build, compiler

    def test_actual_causal_decoder_short_frames_and_state(self):
        overlay, build, compiler = self.native()
        clip = (self.source / 'tools/mtmd/clip.cpp').read_text()
        signature = 'ggml_tensor * clip_graph::build_attn('
        start = clip.index(signature)
        # Include the implementation from the same upstream revision, not a mock
        # attention implementation or an unrelated copy checked into this repo.
        body = clip.index('{', start)
        depth, end = 1, body + 1
        while depth:
            depth += (clip[end] == '{') - (clip[end] == '}')
            end += 1
        attention = self.work / 'attention.cpp'
        attention.write_text('#include "models.h"\n' + clip[start:end] + '\n')
        ggml = build / 'llama/ggml/src'
        binary = self.work / 'decoder'
        subprocess.run([compiler, '-std=c++17', '-O1', '-ffunction-sections', '-fdata-sections',
                        '-I' + str(overlay / 'models'), '-I' + str(overlay),
                        '-I' + str(self.source / 'include'), '-I' + str(self.source / 'ggml/include'),
                        str(ROOT / 'tests/tts-generation/decoder.cpp'), str(attention), str(overlay / 'models/qwen3tts-gen.cpp'),
                        '-Wl,--gc-sections', '-Wl,--start-group',
                        *[str(ggml / name) for name in ('libggml.a', 'libggml-cpu.a', 'libggml-base.a')],
                        '-Wl,--end-group', '-lpthread', '-ldl', '-o', str(binary)], check=True, capture_output=True, text=True)
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
        self.assertIn('samples=1920', result.stdout)
        self.assertIn('samples=138240', result.stdout)
        self.assertIn('chunks', result.stdout)

    def test_actual_helper_auto_and_explicit_language_prefixes(self):
        overlay, build, compiler = self.native()
        binary = self.work / 'language'
        subprocess.run([compiler, '-std=c++17', '-O1', '-I' + str(overlay),
                        '-I' + str(self.source / 'include'), '-I' + str(self.source / 'ggml/include'),
                        str(ROOT / 'tests/tts-generation/language.cpp'), '-L' + str(build),
                        '-lcore', '-Wl,-rpath,' + str(build), '-o', str(binary)],
                       check=True, capture_output=True, text=True)
        result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
        self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
