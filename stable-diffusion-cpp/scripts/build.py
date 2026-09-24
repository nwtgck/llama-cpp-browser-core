#!/usr/bin/env python3
"""Build one isolated image-runtime profile and record exact source provenance."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from prepare_upstream import prepare
ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO / 'llama-cpp/scripts'))
from patch_emscripten import verify_asyncify_bigint_patch

def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()

def main() -> None:
    profiles = json.loads((ROOT / 'config/profiles.json').read_text())
    variants = json.loads((ROOT / 'config/variants.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=list(profiles), required=True)
    parser.add_argument('--variant', choices=list(variants), required=True)
    parser.add_argument('--jobs', type=int, default=min(os.cpu_count() or 2, 4))
    args = parser.parse_args()
    if args.jobs < 1: parser.error('jobs must be positive')
    config = profiles[args.profile]
    upstreams = json.loads((ROOT / 'config/upstreams.json').read_text())
    sources = {}
    for key, pin in upstreams.items():
        source = ROOT / pin['path']
        if not (source / '.git').exists(): parser.error(f'Initialize submodule: {pin["path"]}')
        if git('rev-parse', 'HEAD', cwd=source) != pin['commit']:
            parser.error(f'Unexpected {key} source commit')
        if git('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none', cwd=source):
            parser.error(f'Dirty {key} checkout')
        link = git('ls-tree', 'HEAD', '--', 'stable-diffusion-cpp/' + pin['path']).split()
        if link[:3] != ['160000', 'commit', pin['commit']]: parser.error(f'{key} gitlink differs from pin')
        sources[key] = source
    toolchain = json.loads((REPO / 'llama-cpp/config/toolchain.json').read_text())
    version = subprocess.check_output(['emcc', '--version'], text=True).splitlines()[0]
    if not re.search(r'(?<!\d)' + re.escape(toolchain['emsdkVersion']) + r'(?!\d)', version):
        parser.error('Wrong Emscripten version')
    if config['asyncify']:
        compiler = shutil.which('emcc')
        if compiler is None: parser.error('Missing emcc')
        verify_asyncify_bigint_patch(Path(compiler).resolve().parent, toolchain['emscriptenAsyncifyBigIntPatch'])
    tools = REPO / 'llama-cpp/.tools'
    dawn = tools / 'emdawnwebgpu_pkg'
    if not (dawn / 'emdawnwebgpu.port.py').is_file(): parser.error('Run llama-cpp/scripts/setup_toolchain.py')
    source_commit = git('rev-parse', 'HEAD')
    before = git('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none').splitlines()
    build = ROOT / 'build' / args.profile / args.variant
    prepared = build / 'prepared'
    if prepared.exists(): shutil.rmtree(prepared)
    patch_report = prepare(sources, prepared)
    command = ['emcmake', 'cmake', '-S', str(ROOT), '-B', str(build), '-G', 'Ninja',
               '-DCMAKE_BUILD_TYPE=Release', '-DSDCB_VARIANT=' + args.variant,
               '-DSDCB_SOURCE=' + str(prepared / 'stable-diffusion'),
               '-DSDCB_GGML_SOURCE=' + str(prepared / 'ggml/ggml'),
               '-DSDCB_JSPI=' + ('ON' if config['jspi'] else 'OFF'),
               '-DEMDAWNWEBGPU_DIR=' + str(dawn)]
    subprocess.run(command, cwd=build, check=True)
    subprocess.run(['cmake', '--build', str(build), '--target', 'core', '--parallel', str(args.jobs)], cwd=build, check=True)
    after = git('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none').splitlines()
    provenance = {'runtime': 'stable-diffusion-cpp', 'profile': args.profile, 'variant': args.variant,
                  'sourceCommit': source_commit, 'sourceDirty': bool(before or after),
                  'sourceStatusBeforeBuild': before, 'sourceStatusAfterBuild': after,
                  'upstreams': upstreams, 'configuration': config, 'variantConfiguration': variants[args.variant],
                  'toolchain': toolchain, 'emccVersion': version, 'cmakeCommand': command,
                  'patches': patch_report, 'builtAtUnix': int(time.time()),
                  'validation': {'compiled': True, 'browserSmoke': False, 'realModelInference': False}}
    (build / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    # Only the runtime and provenance cross CI runners, never prepared sources.
    stage = ROOT / 'build/ci-upload' / args.profile / args.variant
    if stage.exists(): shutil.rmtree(stage)
    shutil.copytree(build / 'runtime', stage / 'runtime')
    shutil.copy2(build / 'provenance.json', stage / 'provenance.json')
    if before or after: raise RuntimeError('Source changed; refusing to stage a publishable build')
    print(stage)

if __name__ == '__main__': main()
