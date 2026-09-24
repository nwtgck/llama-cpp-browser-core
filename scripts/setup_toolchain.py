#!/usr/bin/env python3
"""Fetch the pinned emsdk and the Dawn package used by the upstream build."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import urllib.request
import zipfile
from browser_toolchain import load_toolchain
from patch_emscripten import apply_asyncify_bigint_patch

ROOT = Path(__file__).resolve().parents[1]

def run(*command, cwd=None):
    subprocess.run(command, cwd=cwd, check=True)

def prepare_dawn(cfg: dict, tools: Path) -> None:
    """Revalidate the archive on every restore; reconstruct the unpacked port."""
    downloads = tools/'downloads'
    if downloads.is_symlink(): raise RuntimeError('Refusing a linked download directory')
    downloads.mkdir(exist_ok=True)
    archive = downloads/f'emdawnwebgpu_pkg-{cfg["dawnTag"]}.zip'
    if archive.is_symlink(): raise RuntimeError('Refusing a linked Dawn archive')
    if not archive.exists():
        url = f'https://github.com/google/dawn/releases/download/{cfg["dawnTag"]}/{archive.name}'
        temp = archive.with_suffix('.download')
        request = urllib.request.Request(url, headers={'User-Agent':'browser-inference-core-build'})
        with urllib.request.urlopen(request, timeout=120) as response, temp.open('wb') as out:
            while block := response.read(1024*1024): out.write(block)
        temp.replace(archive)
    if archive.is_symlink(): raise RuntimeError('Refusing a linked Dawn archive')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != cfg['dawnSha256']:
        raise RuntimeError('Dawn archive checksum mismatch; refusing extraction')
    # The download is reusable, the extracted port is always reconstructed.
    package = tools/'emdawnwebgpu_pkg'
    if package.is_symlink(): raise RuntimeError('Refusing a linked Dawn package')
    if package.exists(): shutil.rmtree(package)
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (tools/member.filename).resolve()
            if not target.is_relative_to(tools.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('Unsafe archive member')
        z.extractall(tools)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dawn-only', action='store_true')
    args = parser.parse_args()
    cfg = load_toolchain(ROOT)
    tools = ROOT/'.tools'; tools.mkdir(exist_ok=True)
    sdk = tools/'emsdk'
    if not args.dawn_only:
        if not sdk.exists():
            run('git','clone','--depth','1','--branch',cfg['emsdkVersion'],
                'https://github.com/emscripten-core/emsdk.git',str(sdk))
        tags = json.loads((sdk/'emscripten-releases-tags.json').read_text())
        if tags['releases'].get(cfg['emsdkVersion']) != cfg['emscriptenRelease']:
            raise RuntimeError('emsdk release identity differs from the pinned configuration')
        run('./emsdk','install',cfg['emsdkVersion'],cwd=sdk)
        run('./emsdk','activate',cfg['emsdkVersion'],cwd=sdk)
        apply_asyncify_bigint_patch(sdk/'upstream/emscripten', cfg['emscriptenAsyncifyBigIntPatch'])
    prepare_dawn(cfg, tools)
    print('Run: source .tools/emsdk/emsdk_env.sh')
if __name__ == '__main__': main()
