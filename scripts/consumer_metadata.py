#!/usr/bin/env python3
"""Report both runtimes with the existing immutable, attempt-bound PR envelope."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from package_runtime import validate, identity
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / 'llama-cpp/scripts'))
from upstream_provenance import collect
spec = importlib.util.spec_from_file_location('llama_consumer_metadata', ROOT / 'llama-cpp/scripts/consumer_metadata.py')
if spec is None or spec.loader is None: raise RuntimeError('Missing llama report helper')
legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)

def metadata(package: Path, repo: str, commit: str, lock: dict, divergences: dict) -> dict:
    root_manifest = validate(package)
    sd = json.loads((package / 'stable-diffusion-cpp/manifest.json').read_text())
    data = legacy.metadata(package / 'llama-cpp', repo, commit, lock, divergences)
    data['runtime']['manifestFormatVersion'] = root_manifest['formatVersion']
    data['runtime']['llamaManifestPath'] = 'llama-cpp/manifest.json'
    data['retrieval']['sourceRawBase'] += 'llama-cpp/'
    data['retrieval']['sourceRuntimePath'] = 'llama-cpp/'
    data['retrieval']['manifest'] = {'path': 'manifest.json', **identity(package / 'manifest.json')}
    for profile in data['browserProfiles'].values():
        for file in profile.values(): file['path'] = 'llama-cpp/' + file['path']
    for file in data['interfaceFiles']: file['path'] = 'llama-cpp/' + file['path']
    # Link full per-variant provenance by digest instead of repeating toolchain and
    # patch inventories four times in the bounded PR comment.
    data['stableDiffusion'] = {'manifest': {'path': 'stable-diffusion-cpp/manifest.json', **identity(package / 'stable-diffusion-cpp/manifest.json')},
        'abiVersion': sd['abiVersion'], 'upstreams': sd['upstreams'],
        'profiles': {name: {'variants': {variant: {'validation': item['validation'],
            'sourceCommit': item['sourceCommit']} for variant, item in profile['variants'].items()}}
            for name, profile in sd['profiles'].items()},
        'experimental': True,
        'installSeparatelyForNaidan': f'npm install --save-exact stable-diffusion-cpp-browser-core@github:{repo}#{commit}',
        'consumerBoundary': 'Keep the existing llama dependency pinned. The optional image dependency is a separate npm name referencing this same immutable multi-runtime artifact.'}
    return data

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--package', type=Path, required=True)
    p.add_argument('--commit', required=True)
    p.add_argument('--output', type=Path, default=ROOT / 'build/consumer-update')
    a = p.parse_args(); package = a.package.resolve(); validate(package)
    llama = json.loads((package / 'llama-cpp/manifest.json').read_text())
    repo = os.environ['GITHUB_REPOSITORY']
    lock = legacy.generate_lock(repo, a.commit, json.loads((package / 'package.json').read_text())['version'])
    data = metadata(package, repo, a.commit, lock, collect(ROOT / 'llama-cpp', llama))
    markdown = legacy.write_report(a.output, data, os.environ['GITHUB_RUN_ID'], os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    print(markdown)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out: out.write(markdown)
if __name__ == '__main__': main()
