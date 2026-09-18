#!/usr/bin/env python3
"""Fetch the pinned emsdk and the Dawn package used by the upstream build."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def run(*command, cwd=None):
    subprocess.run(command, cwd=cwd, check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dawn-only', action='store_true')
    args = parser.parse_args()
    cfg = json.loads((ROOT/'config/toolchain.json').read_text())
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
    archive = tools/f'emdawnwebgpu_pkg-{cfg["dawnTag"]}.zip'
    if not archive.exists():
        url = f'https://github.com/google/dawn/releases/download/{cfg["dawnTag"]}/{archive.name}'
        temp = archive.with_suffix('.download')
        request = urllib.request.Request(url, headers={'User-Agent':'llama-cpp-browser-core-build'})
        with urllib.request.urlopen(request, timeout=120) as response, temp.open('wb') as out:
            while block := response.read(1024*1024): out.write(block)
        temp.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != cfg['dawnSha256']:
        raise RuntimeError('Dawn archive checksum mismatch; refusing extraction')
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (tools/member.filename).resolve()
            if not target.is_relative_to(tools.resolve()): raise RuntimeError('Unsafe archive member')
        z.extractall(tools)
    print('Run: source .tools/emsdk/emsdk_env.sh')
if __name__ == '__main__': main()
