#!/usr/bin/env python3
"""Prepare the generic language capability and bounded Qwen decoder overlay."""
from __future__ import annotations
import argparse
from pathlib import Path
import shutil
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ('tools/mtmd/clip.cpp', 'tools/mtmd/models/models.h',
         'tools/mtmd/models/qwen3tts-gen.cpp', 'tools/mtmd/mtmd-helper-gen.cpp',
         'tools/mtmd/mtmd-helper.h')


def prepare_tts(source: Path, output: Path, patch: Path | None = None, *,
                capture_output: bool = False) -> Path:
    source, output = source.resolve(), output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError('the TTS overlay must be outside the upstream source tree')
    patch = (patch or ROOT / 'patches/mtmd-tts-generation.patch').resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='tts-overlay-', dir=output) as temporary:
        work = Path(temporary)
        # Mirror all mtmd source/header files so quoted includes and upstream's
        # precompiled models.h cannot mix original and modified class definitions.
        names = []
        for path in sorted((source / 'tools/mtmd').rglob('*')):
            if path.is_symlink():
                raise ValueError('linked files are not supported in the mtmd overlay')
            if path.is_file() and path.suffix in ('.cpp', '.h', '.hpp'):
                name = path.relative_to(source).as_posix()
                names.append(name)
                dest = work / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        if not set(FILES).issubset(names):
            raise ValueError('required TTS sources are missing')
        # Short, occasionally one-sided contexts tolerate blank-line-only upstream
        # formatting changes. Every preimage must still be exact and unique: do
        # not let git choose a similarly named method by approximate line number.
        name = None
        old = []
        def check_preimage():
            if name is not None and old:
                text = ''.join(old)
                if (work / name).read_text().count(text) != 1:
                    raise ValueError('TTS patch context is missing or ambiguous: ' + name)
        for line in patch.read_text().splitlines(keepends=True):
            if line.startswith('--- a/'):
                check_preimage(); old = []; name = line[6:].strip()
                if name not in FILES:
                    raise ValueError('unexpected TTS patch target: ' + name)
            elif line.startswith('@@'):
                check_preimage(); old = []
                if not re.match(r'@@ -[0-9,]+ \+[0-9,]+ @@', line):
                    raise ValueError('invalid TTS patch hunk')
            elif name is not None and line[:1] in (' ', '-') and not line.startswith('---'):
                old.append(line[1:])
        check_preimage()
        # A mismatching upstream revision stops configure; never use a stale copy.
        for args in (['--check'], []):
            subprocess.run(['git', 'apply', '--no-index', '--unidiff-zero', '--whitespace=error', *args, str(patch)],
                           cwd=work, check=True, capture_output=capture_output, text=capture_output)
        for name in names:
            dest = output / name
            if dest.is_symlink() or not dest.parent.resolve().is_relative_to(output):
                raise ValueError('linked destination escapes the TTS overlay')
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or dest.read_bytes() != (work / name).read_bytes():
                (work / name).replace(dest)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare_tts(args.source, args.output)


if __name__ == '__main__':
    main()
