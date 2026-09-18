#!/usr/bin/env python3
"""Assemble a runtime-only npm package and verify every payload file."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
RUNTIME_NAME='llama-cpp-browser-core'


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def validate(directory: Path, require_clean=True):
    directory=directory.resolve()
    package=json.loads((directory/'package.json').read_text())
    for key in ('scripts','workspaces','devDependencies','dependencies','optionalDependencies'):
        if key in package: raise ValueError(f'Unexpected install-time input: {key}')
    if package['name']!=RUNTIME_NAME: raise ValueError('Wrong package name')
    manifest=json.loads((directory/'manifest.json').read_text())
    expected={f['path']:f for f in manifest['files']}
    actual={p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual != set(expected)|{'manifest.json'}: raise ValueError('Manifest does not exactly cover the package tree')
    if (directory/'.gitmodules').exists() or (directory/'binding.gyp').exists(): raise ValueError('Source/build input in runtime')
    if not manifest['profiles']: raise ValueError('No profiles')
    for file in manifest['files']:
        rel=Path(file['path'])
        if rel.is_absolute() or '..' in rel.parts: raise ValueError('Unsafe manifest path')
        path=directory/rel
        if path.is_symlink() or path.stat().st_size!=file['bytes'] or sha(path)!=file['sha256']:
            raise ValueError(f'Invalid payload: {rel}')
        if path.stat().st_size >= 100*1024**2: raise ValueError(f'GitHub single-file size guard exceeded: {rel}')
    for name, info in manifest['profiles'].items():
        if require_clean and info['sourceDirty']: raise ValueError('Cannot publish a dirty source build')
        for ext in ('mjs','wasm'):
            if f'profiles/{name}/core.{ext}' not in expected: raise ValueError(f'Missing {name} runtime')
        if (directory/f'profiles/{name}/core.wasm').read_bytes()[:8] != b'\x00asm\x01\x00\x00\x00':
            raise ValueError('Not a WebAssembly module')
    packed=json.loads(subprocess.check_output(['npm','pack','--dry-run','--json'],cwd=directory,text=True))
    pack_paths={x['path'] for x in packed[0]['files']}
    if pack_paths != actual: raise ValueError(f'npm pack file mismatch: {sorted(actual ^ pack_paths)}')
    return {'files':len(actual),'bytes':sum(p.stat().st_size for p in directory.rglob('*') if p.is_file()),
            'profiles':list(manifest['profiles'])}

def build_package(build_root: Path, destination: Path, profiles: list[str], *, license_roots: list[Path]):
    source=None; upstream=None; schema=None; provenance={}
    with tempfile.TemporaryDirectory(prefix='lcb-package-') as tmp:
        out=Path(tmp)
        for name in profiles:
            build=build_root/name
            data=json.loads((build/'provenance.json').read_text())
            if data['profile']!=name: raise ValueError('Profile mismatch')
            if source is not None and (source!=data['sourceCommit'] or upstream!=data['llamaCommit']):
                raise ValueError('Mixed source commits in one package')
            source=data['sourceCommit']; upstream=data['llamaCommit']; provenance[name]=data
            generated=build/'generated'
            current=(generated/'schema.json').read_bytes()
            if schema is not None and current!=schema: raise ValueError('Mixed binding schemas')
            schema=current
            runtime=out/'profiles'/name
            shutil.copytree(build/'runtime',runtime)
            for p in runtime.rglob('*'):
                if p.is_symlink(): raise ValueError('Unexpected symlink in runtime output')
            if not (out/'api').exists():
                (out/'api').mkdir()
                for file in ('schema.json','schema.mjs','functions.d.ts','exports.json'):
                    shutil.copy2(generated/file,out/'api'/file)
        for file in ('index.mjs','index.d.ts','bindings.mjs','read-only-file.mjs'):
            shutil.copy2(ROOT/'runtime'/file,out/file)
        shutil.copy2(ROOT/'packaging/README.md',out/'README.md')
        shutil.copy2(ROOT/'LICENSE',out/'LICENSE')
        licenses=out/'licenses'; licenses.mkdir()
        copied=0
        for i,root in enumerate(license_roots):
            if not root.exists(): raise ValueError(f'Missing license source: {root}')
            for p in sorted(root.rglob('*')):
                if not p.is_file() or p.is_symlink() or '.git' in p.parts: continue
                if p.name.upper().startswith(('LICENSE','COPYING','COPYRIGHT')):
                    target=licenses/str(i)/p.relative_to(root)
                    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target); copied+=1
        if not copied: raise ValueError('No third-party license notices collected')
        pkg={'name':RUNTIME_NAME,'version':'0.1.0','private':True,'type':'module','license':'MIT',
             'main':'./index.mjs','types':'./index.d.ts',
             'files':['index.mjs','index.d.ts','bindings.mjs','read-only-file.mjs','profiles/','api/','manifest.json','licenses/','README.md','LICENSE'],
             'exports':{'.':{'types':'./index.d.ts','import':'./index.mjs'},
                        './profiles/*':'./profiles/*','./api/*':'./api/*','./manifest.json':'./manifest.json'}}
        (out/'package.json').write_text(json.dumps(pkg,indent=2)+'\n')
        manifest={'formatVersion':1,'sourceCommit':source,'llamaCommit':upstream,'profiles':provenance,
                  'licenseRoots':[p.name for p in license_roots],
                  'files':[{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
                           for p in sorted(out.rglob('*')) if p.is_file()]}
        (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        validate(out,require_clean=False)
        if destination.exists(): shutil.rmtree(destination)
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(out,destination)
    return destination

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-root',type=Path,default=ROOT/'build')
    p.add_argument('--output',type=Path,default=ROOT/'dist/package')
    p.add_argument('--profiles',nargs='+')
    p.add_argument('--license-root',type=Path,action='append')
    p.add_argument('--verify-only',action='store_true')
    a=p.parse_args()
    if not a.verify_only:
        profiles=a.profiles or list(json.loads((ROOT/'config/profiles.json').read_text()))
        roots=a.license_root or [ROOT/'vendor/llama.cpp',ROOT/'.tools/emsdk/upstream/emscripten',ROOT/'.tools/emdawnwebgpu_pkg']
        build_package(a.build_root,a.output,profiles,license_roots=roots)
    print(json.dumps(validate(a.output,require_clean=a.verify_only),indent=2))
if __name__=='__main__': main()
