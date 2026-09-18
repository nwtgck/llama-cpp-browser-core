#!/usr/bin/env python3
"""Record completed CPU browser checks in the corresponding build provenance."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
results=json.loads((ROOT/'build/browser-results.json').read_text())
expected={'cpu-wasm32','cpu-wasm64'}
if {r['profile'] for r in results}!=expected or not all(r['passed'] for r in results):
    raise RuntimeError('Missing or failed CPU browser smoke tests')
for result in results:
    path=ROOT/'build'/result['profile']/'provenance.json'
    value=json.loads(path.read_text())
    value['validation'].update({'browserSmoke':True,'syntheticUntrainedModel':result})
    path.write_text(json.dumps(value,indent=2)+'\n')
