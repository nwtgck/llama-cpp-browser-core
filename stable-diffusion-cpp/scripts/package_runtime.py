#!/usr/bin/env python3
"""Package the image bridge, Wasm, contract, provenance, and complete notices."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
VARIANTS = json.loads((ROOT / 'config/variants.json').read_text())
PROFILES = json.loads((ROOT / 'config/profiles.json').read_text())
NAME = 'stable-diffusion-cpp-browser-core'

def sha(path: Path) -> str:
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()

def validate(directory: Path, require_clean: bool = True) -> dict:
    package = json.loads((directory / 'package.json').read_text())
    if package['name'] != NAME or any(k in package for k in ('scripts', 'dependencies', 'devDependencies', 'optionalDependencies', 'workspaces')):
        raise ValueError('Not a runtime-only image package')
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['formatVersion'] != 1 or manifest['abiVersion'] != 1 or manifest['runtime'] != 'stable-diffusion-cpp':
        raise ValueError('Unknown image runtime format')
    files = manifest['files']
    expected = {entry['path'] for entry in files}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if len(expected) != len(files) or actual != expected | {'manifest.json'}:
        raise ValueError('Manifest must exactly cover the payload')
    for entry in files:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in entry['path']:
            raise ValueError('Unsafe manifest path')
        path = directory / relative
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size != entry['bytes'] or sha(path) != entry['sha256']:
            raise ValueError('Invalid image payload: ' + entry['path'])
        if path.stat().st_size >= 100 * 1024**2: raise ValueError('Artifact single-file size limit exceeded')
    if set(manifest['profiles']) != set(PROFILES): raise ValueError('Incomplete image profiles')
    for profile, info in manifest['profiles'].items():
        if set(info['variants']) != set(VARIANTS): raise ValueError('Incomplete image variants')
        for variant, data in info['variants'].items():
            if data['sourceCommit'] != manifest['sourceCommit'] or data['upstreams'] != manifest['upstreams']:
                raise ValueError('Mixed image source identity')
            if (data['profile'] != profile or data['variant'] != variant or
                    data['configuration'] != PROFILES[profile] or data['variantConfiguration'] != VARIANTS[variant]):
                raise ValueError('Image profile provenance mismatch')
            if data['validation'].get('compiled') is not True or (require_clean and data['sourceDirty'] is not False):
                raise ValueError('Image runtime is uncompiled or dirty')
            if not data.get('patches', {}).get('patches'): raise ValueError('Missing upstream patch provenance')
            for ext in ('mjs', 'wasm', 'd.ts'):
                if f'profiles/{profile}/{variant}/core.{ext}' not in expected: raise ValueError('Missing image runtime')
            if (directory / f'profiles/{profile}/{variant}/core.wasm').read_bytes()[:8] != b'\0asm\1\0\0\0':
                raise ValueError('Not WebAssembly')
    for required in ('LICENSE', 'licenses/stable-diffusion/LICENSE', 'licenses/embedded/json.hpp.txt', 'licenses/embedded/stb_image.h.txt', 'licenses/embedded/stb_image_resize.h.txt', 'licenses/embedded/stb_image_write.h.txt'):
        if required not in expected: raise ValueError('Missing required image license: ' + required)
    if not any(path.startswith('licenses/ggml/') for path in expected): raise ValueError('Missing ggml notices')
    packed = json.loads(subprocess.check_output(['npm', 'pack', '--dry-run', '--json'], cwd=directory, text=True))
    if {entry['path'] for entry in packed[0]['files']} != actual: raise ValueError('npm package tree differs')
    return manifest

def package(build_root: Path, destination: Path, license_roots: list[Path]) -> None:
    sys.path.insert(0, str(ROOT.parent / 'scripts'))
    from package_notices import collect_notices
    profiles = {}; source = None; upstreams = None
    with tempfile.TemporaryDirectory(prefix='sdb-package-') as temporary:
        out = Path(temporary)
        for profile in PROFILES:
            profiles[profile] = {'variants': {}}
            for variant in VARIANTS:
                build = build_root / profile / variant
                data = json.loads((build / 'provenance.json').read_text())
                if source is not None and (source != data['sourceCommit'] or upstreams != data['upstreams']):
                    raise ValueError('Mixed source commits')
                source = data['sourceCommit']; upstreams = data['upstreams']
                profiles[profile]['variants'][variant] = data
                shutil.copytree(build / 'runtime', out / 'profiles' / profile / variant)
        shutil.copy2(ROOT / 'LICENSE', out / 'LICENSE')
        shutil.copy2(ROOT / 'README.md', out / 'README.md')
        sd = ROOT / 'vendor/stable-diffusion.cpp'
        ggml = ROOT / 'vendor/ggml-webgpu-source/ggml'
        collect_notices(sd, out / 'licenses/stable-diffusion')
        collect_notices(ggml, out / 'licenses/ggml')
        (out / 'licenses/embedded').mkdir(parents=True)
        # Preserve the entire embedded notices rather than extract partial licenses.
        for name in ('json.hpp', 'stb_image.h', 'stb_image_resize.h', 'stb_image_write.h'):
            shutil.copy2(sd / 'thirdparty' / name, out / 'licenses/embedded' / (name + '.txt'))
        for i, root in enumerate(license_roots): collect_notices(root, out / 'licenses/toolchain' / str(i))
        pkg = {'name': NAME, 'version': '0.1.0', 'private': True, 'type': 'module', 'license': 'MIT',
               'files': ['profiles/', 'licenses/', 'manifest.json', 'README.md', 'LICENSE'],
               'exports': {'./profiles/*': './profiles/*', './manifest.json': './manifest.json'}}
        (out / 'package.json').write_text(json.dumps(pkg, indent=2) + '\n')
        manifest = {'formatVersion': 1, 'runtime': 'stable-diffusion-cpp', 'abiVersion': 1,
                    'sourceCommit': source, 'upstreams': upstreams, 'profiles': profiles,
                    'experimental': True, 'files': [{'path': p.relative_to(out).as_posix(), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                    for p in sorted(out.rglob('*')) if p.is_file()]}
        (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        validate(out)
        if destination.exists(): shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(out, destination)

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-root', type=Path, default=ROOT / 'build')
    p.add_argument('--output', type=Path, default=ROOT / 'dist/package')
    p.add_argument('--license-root', type=Path, action='append', default=[])
    p.add_argument('--verify-only', action='store_true')
    a = p.parse_args()
    if not a.verify_only: package(a.build_root, a.output, a.license_root)
    print(json.dumps({'sourceCommit': validate(a.output)['sourceCommit']}))
