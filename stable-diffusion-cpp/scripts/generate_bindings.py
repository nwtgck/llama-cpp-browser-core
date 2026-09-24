#!/usr/bin/env python3
"""Generate policy-free bindings for every function/record in the pinned SD public C header."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ABI_VERSION = 2
EXTRA_CONSTANTS = ['EINVAL', 'EIO', 'EOVERFLOW', 'EROFS', 'ENOENT',
                   'SEEK_SET', 'SEEK_CUR', 'SEEK_END']

def walk(node):
    yield node
    for child in node.get('inner', []):
        yield from walk(child)

def generate(source: Path, output: Path, compiler: str) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    unit = output / 'headers.c'
    unit.write_text('#include "stable-diffusion.h"\n')
    command = [compiler, '-x', 'c', '-std=c11', '-fsyntax-only',
               '-I'+str(source/'include'), '-Xclang', '-ast-dump=json', str(unit)]
    ast = json.loads(subprocess.check_output(command, text=True))
    nodes = list(walk(ast))
    aliases = {n['name']: n['type'].get('desugaredQualType', n['type']['qualType'])
               for n in nodes if n.get('kind') == 'TypedefDecl' and 'type' in n}
    declarations = [n for n in ast['inner']
                    if n.get('loc', {}).get('includedFrom', {}).get('file') == str(unit)]
    record_declarations = {n['id']: n for n in declarations
                           if n.get('kind') == 'RecordDecl' and n.get('completeDefinition')}
    named_records = {}
    for n in declarations:
        if n.get('kind') != 'TypedefDecl' or not n['type']['qualType'].startswith(('struct ', 'union ')): continue
        for child in walk(n):
            owned = child.get('ownedTagDecl', {})
            if owned.get('id') in record_declarations:
                named_records[n['name']] = record_declarations[owned['id']]
    for name in named_records: aliases[name] = 'struct ' + name
    def resolve(typ):
        old = None
        while typ != old:
            old = typ
            typ = aliases.get(typ, typ)
        return typ
    def classify(typ):
        t = resolve(typ).strip()
        if '*' in t or t.endswith(']') and '(' in t:
            return 'pointer'
        if re.match(r'^(const )?(struct|union) ', t):
            return 'record'
        if '[' in t:
            return 'array'
        if t == 'void': return 'void'
        if t in ('bool', '_Bool'): return 'boolean'
        if t.startswith('enum '): return 'signed'
        if t in ('float', 'double'): return 'float'
        if typ in ('size_t', 'uintptr_t', 'uint64_t') or t == 'unsigned long' or 'unsigned long long' in t:
            return 'u64'
        if typ in ('ssize_t', 'intptr_t', 'int64_t', 'off_t') or t == 'long' or t == 'long long':
            return 'i64'
        if t in ('unsigned int', 'unsigned short', 'unsigned char', 'unsigned short int'):
            return 'unsigned'
        if t in ('int', 'short', 'short int', 'char', 'signed char'): return 'signed'
        raise ValueError(f'Unsupported C type: {typ!r} -> {t!r}; extend the generator explicitly')
    def js_type(kind):
        if kind in ('pointer', 'record', 'u64', 'i64'): return 'bigint'
        if kind == 'void': return 'void'
        # C bool crosses the low-level boundary as 0 / 1, not JavaScript boolean.
        return 'number'
    def wire_type(kind, typ):
        if kind in ('pointer', 'record', 'u64'): return 'uint64_t'
        if kind == 'i64': return 'int64_t'
        if kind == 'boolean': return 'int32_t'
        if kind == 'signed': return 'int32_t'
        if kind == 'unsigned': return 'uint32_t'
        return typ
    functions, excluded = [], []
    for n in declarations:
        name = n.get('name', '')
        if n.get('kind') != 'FunctionDecl': continue
        if any(x.get('kind') == 'DeprecatedAttr' for x in n.get('inner', [])) or n.get('variadic'):
            excluded.append({'name': name, 'reason': 'deprecated' if not n.get('variadic') else 'variadic'})
            continue
        result = n['type']['qualType'].split(' (', 1)[0]
        rk = classify(result)
        params = []
        for i, p in enumerate(x for x in n.get('inner', []) if x.get('kind') == 'ParmVarDecl'):
            typ = p['type']['qualType']; kind = classify(typ)
            if kind == 'array': raise ValueError(f'Unlowered array argument: {name}')
            params.append({'name': p.get('name', f'arg{i}'), 'cType': typ, 'kind': kind})
        functions.append({'name': name, 'export': '_sdc_'+name, 'returnType': result,
                          'returnKind': rk, 'parameters': params})
    functions = sorted({f['name']: f for f in functions}.values(), key=lambda f: f['name'])
    records = []
    for name, n in sorted(named_records.items()):
        fields = []
        for p in n.get('inner', []):
            if p.get('kind') not in ('FieldDecl', 'IndirectFieldDecl') or not p.get('name'): continue
            if p.get('isBitfield'): raise ValueError(f'Bitfield requires explicit support: {name}.{p["name"]}')
            field_type = p.get('type')
            if field_type is None:
                matches = [f for f in walk(n) if f.get('kind') == 'FieldDecl' and f.get('name') == p['name'] and 'type' in f]
                if len(matches) != 1: raise ValueError(f'Ambiguous anonymous field: {name}.{p["name"]}')
                field_type = matches[0]['type']
            typ = field_type['qualType']
            kind = 'array' if re.search(r'\[[0-9]*\]$', typ) else classify(typ)
            fields.append({'name': p['name'], 'cType': typ, 'kind': kind})
        records.append({'name': name, 'cType': name, 'fields': fields})
    records = sorted({r['name']: r for r in records}.values(), key=lambda r: r['name'])
    enum_names = sorted({child['name'] for n in declarations if n.get('kind') == 'EnumDecl'
                         for child in n.get('inner', []) if child.get('kind') == 'EnumConstantDecl'})
    constants = sorted(set(enum_names + EXTRA_CONSTANTS))
    cpp = ['// Generated from the public header; do not edit.', '#include "stable-diffusion.h"',
           '#include <stdint.h>', '#include <stddef.h>', '#include <stdio.h>',
           '#include <errno.h>', '#include <stdlib.h>', '#include <stdexcept>',
           'static uintptr_t sdc_checked_pointer(uint64_t p) {',
           '  if (p > UINTPTR_MAX) throw std::range_error("pointer/size exceeds address space");',
           '  return (uintptr_t)p;', '}', 'extern "C" {',
           'uint64_t sdc_malloc(uint64_t size) { return (uint64_t)(uintptr_t)malloc(sdc_checked_pointer(size)); }',
           'void sdc_free(uint64_t ptr) { free((void *)sdc_checked_pointer(ptr)); }',
           'uint32_t sdc_pointer_bytes(void) { return sizeof(void *); }',
           f'uint32_t sdc_abi_version(void) {{ return {ABI_VERSION}; }}']
    for f in functions:
        args, callargs, guards = [], [], []
        if f['returnKind'] == 'record':
            args.append('uint64_t out')
            guards.append('if (!out) throw std::invalid_argument("null return storage");')
        for i, p in enumerate(f['parameters']):
            typ, kind = p['cType'], p['kind']; arg = 'a'+str(i)
            args.append(wire_type(kind,typ)+' '+arg)
            if kind == 'record':
                guards.append(f'if (!{arg}) throw std::invalid_argument("null record argument");')
                qualified = typ if typ.startswith('const ') else 'const ' + typ
                callargs.append(f'*(({qualified} *)sdc_checked_pointer({arg}))')
            elif kind == 'pointer': callargs.append(f'({typ})sdc_checked_pointer({arg})')
            elif kind == 'u64' and typ in ('size_t', 'uintptr_t'):
                callargs.append(f'({typ})sdc_checked_pointer({arg})')
            else: callargs.append(f'({typ}){arg}')
        call = f'{f["name"]}({", ".join(callargs)})'
        rk=f['returnKind']; rt=f['returnType']
        if rk == 'record': statement=f'*(({rt} *)sdc_checked_pointer(out)) = {call};'; wt='void'
        elif rk == 'void': statement=call+';'; wt='void'
        elif rk == 'pointer': statement=f'return (uint64_t)(uintptr_t){call};'; wt='uint64_t'
        else: wt=wire_type(rk,rt); statement=f'return ({wt}){call};'
        cpp += [f'{wt} sdc_{f["name"]}({", ".join(args) or "void"}) {{',
                *['  '+g for g in guards], '  '+statement, '}']
    for i,r in enumerate(records): r['id']=i
    for name, expr in [('sizeof_record', 'sizeof({ctype})'), ('alignof_record', 'alignof({ctype})')]:
        cpp += [f'uint64_t sdc_{name}(uint32_t record) {{ switch(record) {{']
        cpp += [f'case {r["id"]}: return {expr.format(ctype=r["cType"])};' for r in records]
        cpp += ['default: return UINT64_MAX; } }']
    for name, expr in [('offsetof_field','offsetof({ctype}, {field})'), ('sizeof_field','sizeof((({ctype} *)0)->{field})')]:
        cpp += [f'uint64_t sdc_{name}(uint32_t record, uint32_t field) {{ switch(record) {{']
        for r in records:
            cpp += [f'case {r["id"]}: switch(field) {{']
            for i,p in enumerate(r['fields']):
                p['id']=i
                cpp += [f'case {i}: return {expr.format(ctype=r["cType"], field=p["name"])};']
            cpp += ['default: return UINT64_MAX; }']
        cpp += ['default: return UINT64_MAX; } }']
    cpp += ['int64_t sdc_constant(uint32_t id) { switch(id) {']
    cpp += [f'case {i}: return (int64_t)({name});' for i,name in enumerate(constants)]
    cpp += ['default: throw std::out_of_range("constant id"); } }', '}']
    helper_names = ['malloc','free','pointer_bytes','abi_version','sizeof_record','alignof_record','offsetof_field','sizeof_field','constant','schema_hash']
    exports = ['_sdc_'+n for n in helper_names] + [f['export'] for f in functions]
    # Direct exports remain available for expert callers bound to this exact native ABI.
    exports += ['_'+f['name'] for f in functions] + ['_malloc','_free']
    (output/'exports.json').write_text(json.dumps(exports,indent=2)+'\n')
    (output/'jspi-exports.json').write_text(json.dumps([f['export'][1:] for f in functions] + [f['name'] for f in functions],indent=2)+'\n')
    schema={'abiVersion':ABI_VERSION,'pointerRepresentation':'bigint-u64','functions':functions,
            'records':records,'constants':constants,'excluded':excluded}
    schema_text=json.dumps(schema,indent=2)+'\n'
    schema_hash=hashlib.sha256(schema_text.encode()).hexdigest()
    cpp.insert(-1, f'uint64_t sdc_schema_hash(void) {{ return (uint64_t)(uintptr_t)"{schema_hash}"; }}')
    (output/'bindings.cpp').write_text(('\n'.join(cpp)+'\n').replace('_Bool', 'bool'))
    (output/'schema.json').write_text(schema_text)
    (output/'schema.mjs').write_text('export default '+json.dumps({**schema, 'schemaSha256':schema_hash},separators=(',',':'))+';\n')
    ts=['/** Generated from the pinned headers. Pointer and size arguments use bigint. */',
        'export interface LowLevelFunctions {']
    for f in functions:
        args=[]
        if f['returnKind']=='record': args.append('out: bigint')
        for i,p in enumerate(f['parameters']): args.append(f'arg{i}: {js_type(p["kind"])}')
        ret='void' if f['returnKind']=='record' else js_type(f['returnKind'])
        ts += [f'  {f["name"]}({", ".join(args)}): Promise<{ret}>;']
    ts += ['}']
    (output/'functions.d.ts').write_text('\n'.join(ts)+'\n')
    return schema

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--clang',default='clang')
    a=p.parse_args(); s=generate(a.source.resolve(),a.output.resolve(),a.clang)
    print(json.dumps({'functions':len(s['functions']),'records':len(s['records']),
                      'constants':len(s['constants']),'excluded':len(s['excluded'])}))
if __name__=='__main__': main()
