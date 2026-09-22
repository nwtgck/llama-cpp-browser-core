#!/usr/bin/env python3
"""Apply the small vision-loader workaround to a build-tree copy, never vendor/."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def prepare(source: Path, output: Path, patch: Path, *, capture_output: bool = False) -> Path:
    # Resolve symlinks before checking overlap so a build path cannot disguise
    # a write inside the pinned checkout (or replace one of its ancestors).
    source = source.resolve()
    output = output.resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("the overlay must be outside the upstream source tree")
    output.mkdir(parents=True, exist_ok=True)
    # Always start from pristine input, including after a previous configure.
    # --check fails closed when an upstream update no longer matches the patch.
    with tempfile.TemporaryDirectory(prefix="mtmd-overlay-", dir=output) as temporary:
        work = Path(temporary)
        shutil.copyfile(source / "tools/mtmd/clip.cpp", work / "clip.cpp")
        # --no-index applies to this scratch directory even though a build tree
        # can be nested in the lcore repository. Never patch the repository root.
        for args in (["--check"], []):
            subprocess.run(["git", "apply", "--no-index", "--whitespace=error", *args,
                            str(patch.resolve())], cwd=work, check=True,
                           capture_output=capture_output, text=capture_output)
        destination = output / "clip.cpp"
        patched = (work / "clip.cpp").read_bytes()
        # Publish only after successful application, leaving the previous copy
        # intact on failure. The exception still aborts configure; a stale copy
        # is not a fallback. Avoid recompiling on a no-op CMake configure.
        if not destination.exists() or destination.read_bytes() != patched:
            (work / "clip.cpp").replace(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source, args.output, ROOT / "patches/mtmd-webgpu-bf16.patch")


if __name__ == "__main__":
    main()
