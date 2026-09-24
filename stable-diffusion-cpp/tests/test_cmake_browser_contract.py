"""Real CMake configuration with mock upstream targets, not a Wasm build."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cmake') and shutil.which('c++'), 'CMake and C++ compiler required')
class BrowserLinkContract(unittest.TestCase):
    def configure(self, variant, jspi):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root/'bridge').mkdir(); (root/'config').mkdir()
        for path in ('config/variants.json','bridge/core.d.ts','bridge/callbacks.js'):
            shutil.copy2(ROOT/path,root/path)
        (root/'bridge/browser.cpp').write_text('// Fixture, never compiled\n')
        (root/'CMakeLists.txt').write_text((ROOT/'CMakeLists.txt').read_text()+'''
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/link-options.txt"
    CONTENT "$<TARGET_PROPERTY:core,LINK_OPTIONS>")
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/defines.txt"
    CONTENT "$<TARGET_PROPERTY:core,COMPILE_DEFINITIONS>")
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/link-inputs.txt"
    CONTENT "$<TARGET_PROPERTY:core,LINK_DEPENDS>")
file(GENERATE OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/backend-options.txt"
    CONTENT "$<TARGET_PROPERTY:ggml-webgpu,INTERFACE_LINK_OPTIONS>")
''')
        sd, ggml = root/'upstream', root/'ggml'
        for path in (sd,ggml): (path/'include').mkdir(parents=True)
        (sd/'include/stable-diffusion.h').touch();(ggml/'include/ggml.h').touch()
        (sd/'stub.cpp').write_text('// No compilation\n')
        (sd/'CMakeLists.txt').write_text('''
add_library(stable-diffusion STATIC stub.cpp)
add_library(ggml-webgpu INTERFACE)
set_property(TARGET ggml-webgpu PROPERTY INTERFACE_LINK_OPTIONS "-exceptions")
''')
        output = root/'out'
        command = ['cmake','-S',str(root),'-B',str(output),'-DEMSCRIPTEN=ON',
                   '-DSDCB_SOURCE='+str(sd),'-DSDCB_GGML_SOURCE='+str(ggml),
                   '-DSDCB_VARIANT='+variant,'-DSDCB_JSPI='+('ON' if jspi else 'OFF')]
        subprocess.run(command,check=True,capture_output=True,text=True,timeout=60)
        return root, {p.stem:p.read_text() for p in output.glob('*.txt')}

    def test_browser_variants_have_no_test_hook_and_keep_both_suspension_modes(self):
        for jspi in (True,False):
            with self.subTest(jspi=jspi):
                root,data = self.configure('browser',jspi)
                self.assertNotIn('_sdb_test_callbacks', data['link-options'])
                self.assertNotIn('SDCB_TEST_HOOKS',data['defines'])
                self.assertIn('--pre-js',data['link-options'])
                self.assertIn(str(root/'bridge/callbacks.js'),data['link-inputs'])
                self.assertIn('-sJSPI=1' if jspi else '-sASYNCIFY=1',data['link-options'])
                incoming=next(part for part in data['link-options'].split(';') if part.startswith('-sINCOMING_MODULE_JS_API'))
                self.assertNotIn('onProgress',incoming);self.assertNotIn('onLog',incoming)
                if not jspi: self.assertNotIn('-exceptions',data['backend-options'])

    def test_test_variants_expose_only_the_explicit_synthetic_callback_hook(self):
        for jspi in (True,False):
            with self.subTest(jspi=jspi):
                _,data = self.configure('test',jspi)
                self.assertIn('SDCB_TEST_HOOKS=1',data['defines'])
                exports=next(part for part in data['link-options'].split(';') if part.startswith('-sEXPORTED_FUNCTIONS'))
                self.assertEqual(exports.count("'_sdb_test_callbacks'"),1)
                self.assertIn("'_sdb_unload','_sdb_test_callbacks']",exports)
                self.assertIn('-sASSERTIONS=1',data['link-options'])


if __name__ == '__main__': unittest.main()
