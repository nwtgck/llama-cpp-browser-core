#!/usr/bin/env python3
"""Describe source overlays for CI reports without changing the runtime manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from github_api import full_sha, git
from prepare_mtmd import prepare
from prepare_tts import prepare_tts, FILES as TTS_FILES


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
    audio_patch = 'patches/mtmd-audio-single-thread.patch'
    audio_source = 'tools/mtmd/mtmd-audio.cpp'
    tts_patch = 'patches/mtmd-tts-generation.patch'
    patch_files = sorted(path.relative_to(root).as_posix() for path in (root / 'patches').rglob('*.patch'))
    enabled = []
    audio_enabled = []
    toolchain = json.loads((root / 'config/toolchain.json').read_text())
    for profile, info in manifest['profiles'].items():
        for variant, provenance in info['variants'].items():
            audio_enabled.append(profile + '/' + variant)
            if provenance['toolchain'] != toolchain:
                raise ValueError('Reporting toolchain differs from compiled provenance')
            options = [option for option in provenance['cmakeCommand']
                       if option.startswith('-DLCB_WEBGPU_BF16_PROJECTOR=')]
            if len(options) != 1 or options[0] not in ('-DLCB_WEBGPU_BF16_PROJECTOR=ON', '-DLCB_WEBGPU_BF16_PROJECTOR=OFF'):
                raise ValueError('Unknown BF16 overlay activation in compiled provenance')
            if options[0].endswith('=ON'):
                enabled.append(profile + '/' + variant)
    with tempfile.TemporaryDirectory(prefix='lcb-report-overlay-') as temporary:
        tts = prepare_tts(vendor, Path(temporary) / 'tts', root / tts_patch)
        tts_copies = {name: file_identity(tts / name) for name in TTS_FILES}
        patched = prepare(tts, Path(temporary) / 'overlay', root / patch_path)
        patched_identity = file_identity(patched)
        audio = prepare(vendor, Path(temporary) / 'audio', root / audio_patch, filename='mtmd-audio.cpp')
        audio_identity = file_identity(audio)
    supporting = ['scripts/prepare_mtmd.py', 'cmake/MtmdOverlay.cmake',
                  'bridge/mtmd-bf16.h', 'docs/webgpu-bf16-projector.md']
    return {
        'upstreamRepository': 'ggml-org/llama.cpp',
        'baseCommit': upstream,
        'vendorCheckoutModified': False,
        'inventoryScope': 'Known build-tree overlays plus every *.patch file under patches/; not an exhaustive compiler transformation inventory.',
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
                'inputOverlay': 'tts-generation',
                'enabledProfileVariants': sorted(enabled),
            },
            'supportingFiles': {path: file_identity(root / path) for path in supporting},
            'searchHints': ['mtmd-webgpu-bf16', 'LCB_WEBGPU_BF16_PROJECTOR', 'lcb_clip', 'bf16-f32', 'cpu_bf16'],
            'behavior': 'WebGPU vision BF16 weights become resident F32 weights (2x storage for converted weights); bounded upload, placement diagnostics and loader/allocation guards. Model files remain unchanged.',
        }, {
            'id': 'single-thread-wasm-audio-preprocessing',
            'kind': 'build-tree-source-overlay',
            'upstreamSource': {'path': audio_source, **file_identity(vendor / audio_source)},
            'patch': {'path': audio_patch, **file_identity(root / audio_patch)},
            'compiledCopy': {'logicalUpstreamPath': audio_source, **audio_identity},
            'application': {
                'preparationScript': 'scripts/prepare_mtmd.py --component audio',
                'cmakeHook': 'cmake/MtmdAudioOverlay.cmake',
                'compileGuard': 'defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__)',
                'enabledProfileVariants': sorted(audio_enabled),
            },
            'supportingFiles': {path: file_identity(root / path) for path in
                                ['scripts/prepare_mtmd.py', 'cmake/MtmdAudioOverlay.cmake',
                                 'docs/audio-single-thread.md']},
            'searchHints': ['mtmd-audio-single-thread', '__EMSCRIPTEN_PTHREADS__', 'log_mel_spectrogram'],
            'behavior': 'Execute shared mel and Parakeet preprocessing serially in non-pthread Emscripten builds. Native and pthread worker loops are unchanged.',
        }, {
            'id': 'tts-generation',
            'kind': 'build-tree-source-overlay',
            'upstreamSources': {name: file_identity(vendor / name) for name in TTS_FILES},
            'patch': {'path': tts_patch, **file_identity(root / tts_patch)},
            'copiesAfterThisOverlay': tts_copies,
            'subsequentOverlays': {'tools/mtmd/clip.cpp': 'webgpu-vision-bf16-projector when enabled; see its compiledCopy for the final bytes'},
            'application': {
                'preparationScript': 'scripts/prepare_tts.py',
                'cmakeHook': 'cmake/MtmdTtsOverlay.cmake',
                'enabledProfileVariants': sorted(audio_enabled),
            },
            'supportingFiles': {path: file_identity(root / path) for path in
                                ['scripts/prepare_tts.py', 'cmake/MtmdTtsOverlay.cmake',
                                 'scripts/generate_bindings.py', 'docs/tts-generation.md']},
            'searchHints': ['mtmd-tts-generation', 'supports_language_auto', 'n_frames'],
            'behavior': 'Decode the actual Qwen waveform frame count without changing its attention window; expose generic automatic-language capability. Other mtmd source/header files are mirrored unchanged to keep quoted includes and precompiled headers coherent.',
        }],
        'otherPatchFiles': {path: {'application': 'not classified by this report', **file_identity(root / path)}
                            for path in patch_files if path not in (patch_path, audio_patch, tts_patch)},
        'toolchainDivergences': {
            'emscriptenAsyncifyBigInt': {
                'scope': 'Emscripten runtime, not upstream llama.cpp',
                'implementation': {'path': 'scripts/patch_emscripten.py', **file_identity(root / 'scripts/patch_emscripten.py')},
                'emscriptenRelease': toolchain['emscriptenRelease'],
                'guardPins': toolchain['emscriptenAsyncifyBigIntPatch'],
            },
        },
    }
