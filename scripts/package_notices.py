"""Shared license-file copying (no inference or package-schema assumptions)."""
from pathlib import Path
import shutil

def collect_notices(source: Path, destination: Path) -> None:
    count = 0
    if not source.is_dir(): raise ValueError(f'Missing notice source: {source}')
    for path in sorted(source.rglob('*')):
        if path.is_file() and not path.is_symlink() and '.git' not in path.parts and path.name.upper().startswith(('LICENSE', 'COPYING', 'COPYRIGHT')):
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target); count += 1
    if not count: raise ValueError(f'No license notices: {source}')
