#!/usr/bin/env python3
"""Describe source overlays for CI reports without changing the runtime manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from github_api import full_sha, git
from prepare_mtmd import prepare


def file_identity(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing or linked provenance input: {path}')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'bytes': path.stat().st_size, 'sha256': digest}


def collect(root: Path, manifest: dict) -> dict:
    source = full_sha(manifest['sourceCommit'])
    upstream = full_sha(manifest['llamaCommit'])
    if git('rev-parse', 'HEAD', cwd=root).stdout.strip() != source:
        raise ValueError('Reporting checkout does not match the built source commit')
    vendor = root / 'vendor/llama.cpp'
    if git('rev-parse', 'HEAD', cwd=vendor).stdout.strip() != upstream:
        raise ValueError('Reporting submodule does not match the built upstream commit')
    patch_path = 'patches/mtmd-webgpu-bf16.patch'
    upstream_path = 'tools/mtmd/clip.cpp'
    patch_files = sorted(path.relative_to(root).as_posix() for path in (root / 'patches').rglob('*.patch'))
    enabled = []
    toolchain = json.loads((root / 'config/toolchain.json').read_text())
    for profile, info in manifest['profiles'].items():
        for variant, provenance in info['variants'].items():
            if provenance['toolchain'] != toolchain:
                raise ValueError('Reporting toolchain differs from compiled provenance')
            options = [option for option in provenance['cmakeCommand']
                       if option.startswith('-DLCB_WEBGPU_BF16_PROJECTOR=')]
            if len(options) != 1 or options[0] not in ('-DLCB_WEBGPU_BF16_PROJECTOR=ON', '-DLCB_WEBGPU_BF16_PROJECTOR=OFF'):
                raise ValueError('Unknown BF16 overlay activation in compiled provenance')
            if options[0].endswith('=ON'):
                enabled.append(profile + '/' + variant)
    with tempfile.TemporaryDirectory(prefix='lcb-report-overlay-') as temporary:
        patched = prepare(vendor, Path(temporary) / 'overlay', root / patch_path)
        patched_identity = file_identity(patched)
    supporting = ['scripts/prepare_mtmd.py', 'cmake/MtmdOverlay.cmake',
                  'bridge/mtmd-bf16.h', 'docs/webgpu-bf16-projector.md']
    return {
        'upstreamRepository': 'ggml-org/llama.cpp',
        'baseCommit': upstream,
        'vendorCheckoutModified': False,
        'inventoryScope': 'Known build-tree overlay plus every *.patch file under patches/; not an exhaustive compiler transformation inventory.',
        'sourceOverlays': [{
            'id': 'webgpu-vision-bf16-projector',
            'kind': 'build-tree-source-overlay',
            'upstreamSource': {'path': upstream_path, **file_identity(vendor / upstream_path)},
            'patch': {'path': patch_path, **file_identity(root / patch_path)},
            'compiledCopy': {'logicalUpstreamPath': upstream_path, **patched_identity},
            'application': {
                'preparationScript': 'scripts/prepare_mtmd.py',
                'cmakeHook': 'cmake/MtmdOverlay.cmake',
                'option': 'LCB_WEBGPU_BF16_PROJECTOR',
                'enabledProfileVariants': sorted(enabled),
            },
            'supportingFiles': {path: file_identity(root / path) for path in supporting},
            'searchHints': ['mtmd-webgpu-bf16', 'LCB_WEBGPU_BF16_PROJECTOR', 'lcb_clip', 'bf16-f32', 'cpu_bf16'],
            'behavior': 'WebGPU vision BF16 weights become resident F32 weights (2x storage for converted weights); bounded upload, placement diagnostics and loader/allocation guards. Model files remain unchanged.',
        }],
        'otherPatchFiles': {path: {'application': 'not classified by this report', **file_identity(root / path)}
                            for path in patch_files if path != patch_path},
        'toolchainDivergences': {
            'emscriptenAsyncifyBigInt': {
                'scope': 'Emscripten runtime, not upstream llama.cpp',
                'implementation': {'path': 'scripts/patch_emscripten.py', **file_identity(root / 'scripts/patch_emscripten.py')},
                'emscriptenRelease': toolchain['emscriptenRelease'],
                'guardPins': toolchain['emscriptenAsyncifyBigIntPatch'],
            },
        },
    }
