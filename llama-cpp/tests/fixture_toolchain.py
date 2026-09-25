"""Create the actual monorepo toolchain layout in isolated test directories."""
import json
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT.parent


def merged_toolchain():
    return {**json.loads((COMMON / 'toolchain/config.json').read_text()),
            **json.loads((ROOT / 'config/toolchain.json').read_text())}


def seed_toolchain(runtime, values=None, *, scripts=False):
    values = merged_toolchain() if values is None else values
    local_keys = {'llamaCommit', 'artifactBranch'}
    (runtime / 'config').mkdir(parents=True, exist_ok=True)
    (runtime.parent / 'toolchain').mkdir(parents=True, exist_ok=True)
    (runtime / 'config/toolchain.json').write_text(json.dumps({k: v for k, v in values.items() if k in local_keys}))
    (runtime.parent / 'toolchain/config.json').write_text(json.dumps({k: v for k, v in values.items() if k not in local_keys}))
    if scripts:
        target = runtime.parent / 'scripts'; target.mkdir(parents=True, exist_ok=True)
        for name in ('browser_toolchain.py', 'patch_emscripten.py'):
            shutil.copy2(COMMON / 'scripts' / name, target / name)
