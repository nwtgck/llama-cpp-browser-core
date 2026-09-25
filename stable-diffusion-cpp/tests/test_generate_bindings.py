import importlib.util
from pathlib import Path
import tempfile
import shutil
import subprocess
import json
import unittest
ROOT = Path(__file__).resolve().parents[1]
class Bindings(unittest.TestCase):
    @unittest.skipUnless(shutil.which('clang') and shutil.which('c++'), 'native compilers required')
    def test_anonymous_records_callbacks_and_full_width_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'include').mkdir()
            (root/'include/stable-diffusion.h').write_text('''#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
typedef struct { int64_t seed; const char *path; bool enabled; size_t count; } params_t;
typedef void (*progress_t)(int, int, float, void *);
void configure(params_t *p);
void set_progress(progress_t cb, void *data);
''')
            subprocess.run(['python3',str(ROOT/'scripts/generate_bindings.py'),'--source',str(root),'--output',str(root/'out')],check=True,capture_output=True)
            schema = json.loads((root/'out/schema.json').read_text())
            self.assertEqual([r['name'] for r in schema['records']], ['params_t'])
            self.assertEqual(schema['abiVersion'],2)
            self.assertEqual(len(schema['functions']),2)
            self.assertIn('uint64_t', (root/'out/bindings.cpp').read_text())
            subprocess.run(['c++','-std=c++17','-fsyntax-only','-I'+str(root/'include'),str(root/'out/bindings.cpp')],check=True,capture_output=True)
if __name__ == '__main__': unittest.main()
