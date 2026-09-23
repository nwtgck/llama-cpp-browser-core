"""Exercise actual preprocessing; no model, GPU, or Emscripten claim is made."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_mtmd import prepare


class AudioOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lcb-audio-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.upstream = Path(os.environ.get('LCB_TEST_LLAMA_SOURCE', ROOT / 'vendor/llama.cpp'))

    def test_only_known_translation_units_are_accepted(self):
        for name in ('../clip.cpp', '/tmp/clip.cpp', 'unknown.cpp'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'translation unit'):
                prepare(self.work / 'source', self.work / 'build', self.work / 'no.patch', filename=name)

    def real_source(self):
        source = self.upstream / 'tools/mtmd/mtmd-audio.cpp'
        if not source.is_file():
            self.skipTest('Initialize the pinned submodule or set LCB_TEST_LLAMA_SOURCE')
        return source

    def test_real_audio_overlay_is_idempotent_and_preserves_input(self):
        source = self.real_source()
        before = source.read_bytes()
        patch = ROOT / 'patches/mtmd-audio-single-thread.patch'
        target = prepare(self.upstream, self.work / 'build', patch, filename='mtmd-audio.cpp')
        stamp = target.stat().st_mtime_ns
        prepare(self.upstream, self.work / 'build', patch, filename='mtmd-audio.cpp')
        self.assertEqual(target.stat().st_mtime_ns, stamp)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(target.read_text().count('#if defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__)'), 2)

    def test_actual_preprocessing_matches_without_creating_threads(self):
        source = self.real_source()
        compiler = os.environ.get('LCB_TEST_CXX') or shutil.which('clang++') or shutil.which('g++')
        if not compiler:
            self.skipTest('A native C++ compiler is required')
        target = prepare(self.upstream, self.work / 'overlay', ROOT / 'patches/mtmd-audio-single-thread.patch', filename='mtmd-audio.cpp')
        stubs = self.work / 'no-threads'
        stubs.mkdir()
        # The serial branch must not even attempt to construct a worker. Keep
        # the interface compilable so an accidental dispatch fails at runtime.
        (stubs / 'thread').write_text('''#pragma once
#include <cstdlib>
namespace std {
class thread {
public:
    thread() = default;
    template<class F, class... Args> explicit thread(F &&, Args &&...) { std::abort(); }
    void join() { std::abort(); }
};
}
''')
        outputs = []
        for name, unit, defines in [
            ('upstream', source, []),
            ('native-overlay', target, []),
            ('single-thread', target, ['-D__EMSCRIPTEN__=1', '-I' + str(stubs)]),
            ('pthread', target, ['-D__EMSCRIPTEN__=1', '-D__EMSCRIPTEN_PTHREADS__=1']),
        ]:
            with self.subTest(configuration=name):
                binary = self.work / name
                command = [compiler, '-std=c++17', '-O1', '-pthread', *defines,
                           '-I' + str(self.upstream / 'tools/mtmd'),
                           '-I' + str(self.upstream / 'include'), '-I' + str(self.upstream / 'ggml/include'),
                           str(unit), str(ROOT / 'tests/audio-preprocessing/main.cpp'), '-o', str(binary)]
                subprocess.run(command, check=True, capture_output=True, text=True)
                result = subprocess.run([str(binary)], check=True, capture_output=True)
                self.assertGreater(len(result.stdout), 1000)
                outputs.append(result.stdout)
        self.assertEqual(len(outputs), 4)
        self.assertTrue(all(output == outputs[0] for output in outputs[1:]), 'Serial execution must compute every frame identically')


if __name__ == '__main__':
    unittest.main()
