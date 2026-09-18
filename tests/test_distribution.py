"""Use empty Wasm fixtures to test distribution, not inference."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from package_runtime import build_package, validate
from publish_artifacts import publish


def git(*args,cwd=None):
    return subprocess.check_output(['git',*args],cwd=cwd,text=True).strip()

class Distribution(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='lcb-test-')
        self.root=Path(self.tmp.name); build=self.root/'build/cpu-wasm32'
        (build/'runtime').mkdir(parents=True); (build/'generated').mkdir()
        (build/'runtime/core.wasm').write_bytes(b'\0asm\1\0\0\0')
        (build/'runtime/core.mjs').write_text('export default async () => ({ fixture: true });\n')
        for name,content in {'schema.json':'{}','schema.mjs':'export default {};',
                             'functions.d.ts':'export interface LowLevelFunctions {}', 'exports.json':'[]'}.items():
            (build/'generated'/name).write_text(content)
        self.provenance={'profile':'cpu-wasm32','sourceCommit':'a'*40,'llamaCommit':'b'*40,'sourceDirty':False,
                         'validation':{'fixtureOnly':True}}
        (build/'provenance.json').write_text(json.dumps(self.provenance))
        licenses=self.root/'notices'; licenses.mkdir(); (licenses/'LICENSE').write_text('Test-only notice')
        self.licenses=licenses; self.package=self.root/'package'
        self.assemble()
    def tearDown(self): self.tmp.cleanup()
    def assemble(self):
        return build_package(self.root/'build',self.package,['cpu-wasm32'],license_roots=[self.licenses])
    def test_runtime_has_no_build_hooks_or_submodule(self):
        result=validate(self.package)
        self.assertEqual(result['profiles'],['cpu-wasm32'])
        package=json.loads((self.package/'package.json').read_text())
        self.assertNotIn('scripts',package)
        self.assertNotIn('devDependencies',package)
        self.assertFalse((self.package/'.gitmodules').exists())
    def test_payload_tampering_is_rejected(self):
        (self.package/'profiles/cpu-wasm32/core.wasm').write_bytes(b'bad')
        with self.assertRaises(ValueError): validate(self.package)
    def test_extra_file_is_rejected(self):
        (self.package/'unlisted').write_text('stale asset')
        with self.assertRaises(ValueError): validate(self.package)
    def test_dirty_source_cannot_be_published(self):
        path=self.root/'build/cpu-wasm32/provenance.json'
        value=json.loads(path.read_text()); value['sourceDirty']=True; path.write_text(json.dumps(value)); self.assemble()
        with self.assertRaises(ValueError): validate(self.package)
    def test_append_only_publication_preserves_old_sha_and_npm_install(self):
        remote=self.root/'remote.git'; git('init','--bare','--quiet',str(remote))
        first=publish(self.package,str(remote))
        # Change a payload and remove an old auxiliary asset by repackaging from scratch.
        (self.root/'build/cpu-wasm32/runtime/core.mjs').write_text('export default async () => ({ fixture: 2 });\n')
        self.assemble()
        second=publish(self.package,str(remote))
        self.assertNotEqual(first,second)
        for commit in (first,second):
            self.assertEqual(git('--git-dir='+str(remote),'show','-s','--format=%an <%ae>',commit),
                             'Ryo Ota <nwtgck@nwtgck.org>')
            self.assertEqual(git('--git-dir='+str(remote),'show','-s','--format=%cn <%ce>',commit),
                             'github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>')
            self.assertEqual(git('--git-dir='+str(remote),'show','-s','--format=%s',commit),
                             'build(artifacts): publish runtime from '+self.provenance['sourceCommit'][:12])
            body=git('--git-dir='+str(remote),'show','-s','--format=%B',commit)
            self.assertIn('Source-commit: '+self.provenance['sourceCommit'],body)
            self.assertEqual(body.count('Co-authored-by: ChatGPT <noreply@openai.com>'),1)
        self.assertEqual(git('--git-dir='+str(remote),'rev-parse',second+'^'),first)
        self.assertEqual(git('--git-dir='+str(remote),'rev-list','--max-parents=0',second),first)
        self.assertNotIn('160000',git('--git-dir='+str(remote),'ls-tree','-r',second))
        install=self.root/'consumer'; install.mkdir()
        (install/'package.json').write_text('{"private":true}')
        command=['npm','install','--no-audit','--no-fund',f'git+file://{remote}#{first}']
        subprocess.run(command,cwd=install,check=True,capture_output=True,text=True)
        copied=install/'node_modules/llama-cpp-browser-core/profiles/cpu-wasm32/core.mjs'
        self.assertIn('fixture: true',copied.read_text())
        shutil.rmtree(install/'node_modules')
        subprocess.run(['npm','ci','--no-audit','--no-fund','--cache',str(self.root/'empty-cache')],
                       cwd=install,check=True,capture_output=True,text=True)
        self.assertIn('fixture: true',copied.read_text())
    def test_source_branch_publication_is_rejected(self):
        with self.assertRaises(ValueError): publish(self.package,str(self.root/'unused.git'),'main')

if __name__=='__main__': unittest.main()
