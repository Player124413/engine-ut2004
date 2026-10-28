#!/usr/bin/env python3
# conform_layout.py - reorder generated C++ class/struct members to follow
# Shrek 2's script property chain order, so script offsets (assigned by
# UStruct::Link in chain order) match C++ member offsets. Members only in the
# C++ class are appended after the script members; script-only properties get
# freshly declared members. The new block is guarded by #if UE_SHREK2, the
# original declaration order stays in the #else branch.
#
# Usage: conform_layout.py <shrek-System-dir> [--apply] [--show ClassName ...]
import re, sys, os, glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upkg2 import Pkg, PROP_TYPES

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

MEM_RE = re.compile(r'^(.+?)\s+(\w+)\s*(\[[^\]]*\])?\s*(:\s*1)?\s*(GCC_PACK\(\d+\))?\s*;$')
CLASS_RE = re.compile(r'^(class|struct)\s+\w+_API\s+(\w+)(?:\s*:\s*public\s+(\w+))?\s*(\{)?\s*$')

# ---------------- Shrek side ----------------

def prop_detail(pkg, bod, e):
    """Full type info for one property export."""
    b = bod.get(e['index'], {})
    d = dict(name=e['name'], kind=e['class'], arraydim=b.get('arraydim', 1),
             enum=b.get('enum'), propclass=b.get('propclass'), struct=b.get('struct'),
             metaclass=b.get('metaclass'), count=b.get('count'))
    inner = b.get('inner')
    if inner:
        # 'inner' is the export index of the nested property object.
        ie = pkg.exports[inner - 1] if 0 < inner <= len(pkg.exports) else None
        d['inner'] = prop_detail(pkg, bod, ie) if ie else None
    return d

def shrek_types(sysdir):
    classes, structs, dupes = {}, {}, []
    for path in sorted(glob.glob(os.path.join(sysdir, '*.u'))):
        pkg = Pkg(path)
        bod = pkg.bodies()
        for e in pkg.exports:
            if e['class'] not in ('Class', 'Struct'):
                continue
            props = []
            for k in pkg.children_of(bod, e['index']):
                if k['class'] in PROP_TYPES:
                    props.append(prop_detail(pkg, bod, k))
            rec = dict(pkg=pkg.name, super=e['super'], props=props,
                       native=bool(bod.get(e['index'], {}).get('classflags', 0) & 0x10))
            tgt = classes if e['class'] == 'Class' else structs
            if e['name'] in tgt:
                prev = tgt[e['name']]
                if [p['name'] for p in prev['props']] != [p['name'] for p in props]:
                    dupes.append((e['name'], prev['pkg'], pkg.name))
            else:
                tgt[e['name']] = rec
    return classes, structs, dupes

# ---------------- Header side ----------------

def member_of(line):
    """Return dict(type,name,array,bitfield,raw) if line is a member decl."""
    s = line.strip()
    if not s or s.startswith('//') or '{' in s or '}' in s:
        return None
    s = re.sub(r'//.*', '', s).strip()
    m = MEM_RE.match(s)
    if not m:
        return None
    typ, name = m.group(1).strip(), m.group(2)
    if '(' in typ or ')' in typ or '=' in typ:
        return None
    first = typ.split()[0] if typ.split() else ''
    if first in ('typedef', 'using', 'friend', 'return', 'static', 'virtual', 'enum', 'template'):
        return None
    return dict(type=typ, name=name, array=m.group(3) or '',
                bitfield=bool(m.group(4)), raw=line.rstrip('\n'))

def parse_header(path):
    """Parse a generated header: list of blocks (class/struct definitions)."""
    lines = open(path, encoding='latin-1').read().split('\n')
    blocks = []
    i = 0
    while i < len(lines):
        m = CLASS_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        kind, name, sup = m.group(1), m.group(2), m.group(3)
        start = i
        if not m.group(4):
            # '{' on one of the next lines
            j = i + 1
            while j < len(lines) and '{' not in lines[j]:
                j += 1
            if j >= len(lines):
                i += 1
                continue
            i = j
        body_start = i + 1
        depth = 1 + lines[i].count('{') - lines[i].count('}') - 1  # depth just inside '{'
        i += 1
        member_idxs, members, brace_depth = [], [], 1
        end = None
        decl_end = None
        while i < len(lines):
            line = lines[i]
            if brace_depth == 1 and lines[i].strip() == '};':
                end = i
                break
            if decl_end is None and ('DECLARE_CLASS(' in line or 'DECLARE_ABSTRACT_CLASS(' in line):
                decl_end = i
            opens, closes = line.count('{'), line.count('}')
            if brace_depth == 1 and opens == 0 and closes == 0 and (decl_end is None):
                md = member_of(line)
                if md:
                    member_idxs.append(i)
                    members.append(md)
            brace_depth += opens - closes
            i += 1
        if end is None:
            break
        blocks.append(dict(kind=kind, name=name, super=sup, start=start,
                           body_start=body_start, end=end,
                           member_idxs=member_idxs, members=members))
        i = end + 1
    return lines, blocks

def strip_prefix(cppname):
    if len(cppname) > 1 and cppname[0] in 'AUF' and cppname[1].isupper():
        return cppname[1:]
    return cppname

# ---------------- Resolver ----------------

class Resolver:
    def __init__(self, headers, shk_structs):
        self.cpp_by_script = {}
        self.struct_defs = {}      # cpp struct name -> header block rec (generated)
        self.shk_structs = shk_structs
        self.synth_structs = {}    # script struct name -> generated C++ text
        # core/engine classes not in generated headers
        for n in ('UObject', 'UClass', 'UPackage', 'UField', 'UStruct', 'UFunction',
                  'UState', 'UEnum', 'UConst', 'UProperty', 'UCommandlet'):
            self.cpp_by_script[strip_prefix(n)] = n
        for blocks in headers.values():
            for bl in blocks:
                self.cpp_by_script.setdefault(strip_prefix(bl['name']), bl['name'])
                if bl['kind'] == 'struct':
                    self.struct_defs.setdefault(bl['name'], bl)
        # scan every header for plain struct/class F... definitions
        self.known_types = set(self.struct_defs)
        for h in glob.glob(os.path.join(REPO, 'Source', '**', 'Inc', '*.h'), recursive=True):
            try:
                t = open(h, encoding='latin-1').read()
            except OSError:
                continue
            for mm in re.finditer(r'\b(?:struct|class)\s+(?:\w+_API\s+)?((?:F|E)\w+)\s*(?::\s*public\s+\w+\s*)?\{', t):
                self.known_types.add(mm.group(1))

    def cpp_class(self, script_name):
        return self.cpp_by_script.get(script_name, 'UObject')

    def struct_cpp(self, script_name):
        for cand in ('F' + script_name, script_name):
            if cand in self.known_types:
                return cand
        if script_name in self.shk_structs:
            return 'F' + script_name  # will be synthesized on demand
        return None

    def decl_type(self, pd, depth=0):
        """C++ element type for a Shrek property (no name/array suffix)."""
        k = pd['kind']
        if k == 'ByteProperty': return 'BYTE', None
        if k == 'IntProperty': return 'INT', None
        if k == 'FloatProperty': return 'FLOAT', None
        if k == 'BoolProperty': return 'BITFIELD', None
        if k == 'NameProperty': return 'FName', None
        if k == 'StrProperty': return 'FString', None
        if k == 'DelegateProperty': return 'FScriptDelegate', None
        if k == 'PointerProperty': return 'PTRINT', None
        if k == 'ObjectProperty':
            return 'class %s*' % self.cpp_class(pd.get('propclass') or 'Object'), None
        if k == 'ClassProperty':
            return 'class UClass*', None
        if k == 'StructProperty':
            sn = pd.get('struct') or '?'
            cpp = self.struct_cpp(sn)
            if cpp is None:
                return None, 'unknown struct %s' % sn
            if cpp not in self.known_types and sn in self.shk_structs:
                self.ensure_synth_struct(sn, depth)
            return cpp, None
        if k == 'ArrayProperty':
            inner = pd.get('inner')
            if not inner:
                return None, 'array without inner'
            it, warn = self.decl_type(inner, depth + 1)
            if it is None:
                return None, warn
            return 'TArray<%s>' % it, None
        if k == 'FixedArrayProperty':
            inner = pd.get('inner')
            if not inner:
                return None, 'fixed array without inner'
            return self.decl_type(inner, depth + 1)
        return None, 'unhandled property type %s' % k

    def ensure_synth_struct(self, script_name, depth=0):
        if script_name in self.synth_structs or depth > 8:
            return
        rec = self.shk_structs.get(script_name)
        if not rec:
            return
        self.synth_structs[script_name] = None  # cycle guard
        lines = []
        for p in rec['props']:
            lines.append(self.synth_decl(p) or '    // ?? %s' % p['name'])
        lines = gcc_pack_lines(lines, rec['props'])
        self.synth_structs[script_name] = 'struct F%s\n{\n%s\n};' % (
            script_name, '\n'.join(lines))

    def synth_decl(self, p):
        t, warn = self.decl_type(p)
        if t is None:
            return None
        arr = ''
        if p['kind'] == 'FixedArrayProperty':
            arr = '[%i]' % (p.get('count') or 1)
        elif (p.get('arraydim') or 1) > 1:
            arr = '[%i]' % p['arraydim']
        if p['kind'] == 'BoolProperty':
            if arr:
                return None
            return '    BITFIELD %s:1;' % p['name']
        return '    %s %s%s;' % (t, p['name'], arr)

# script property element sizes (bytes, LP64), for sanity warnings only
SCRIPT_SIZE = {'ByteProperty': 1, 'IntProperty': 4, 'FloatProperty': 4,
               'NameProperty': 4, 'ObjectProperty': 8, 'ClassProperty': 8,
               'PointerProperty': 8, 'StrProperty': 16, 'ArrayProperty': 16,
               'DelegateProperty': 16, 'BoolProperty': 4}
CPP_SCALAR_SIZE = {'BYTE': 1, 'INT': 4, 'FLOAT': 4, 'FName': 4, 'DWORD': 4,
                   'UBOOL': 4, 'PTRINT': 8, 'DOUBLE': 8, 'QWORD': 8, 'SQWORD': 8,
                   'SWORD': 2, '_WORD': 2, 'SBYTE': 1}

def cpp_type_size(typ):
    typ = typ.replace('const ', '')
    if '*' in typ:
        return 8
    if typ in CPP_SCALAR_SIZE:
        return CPP_SCALAR_SIZE[typ]
    if typ.startswith(('TArray', 'FString')):
        return 16
    if typ == 'FScriptDelegate':
        return 16
    return None

# ---------------- Conformance ----------------

def gcc_pack_lines(lines, props):
    """GCC under #pragma pack(4) packs bitfields byte-tight and lets following
    members into the slack, while script linking reserves whole 4-byte words
    for bool runs. Force 4-byte alignment on the first bool of each run and on
    the first member following a run so C++ offsets match script offsets
    (the same mechanism the stock headers use)."""
    out = []
    prev_bool = False
    after_run = False
    by_name = {}
    for ln in lines:
        m = re.search(r'\b(\w+)(\[\d+\])?\s*(:\s*1)?\s*;', ln)
        if m:
            by_name.setdefault(m.group(1), ln)
    order = []
    for p in props:
        ln = by_name.get(p['name'])
        if ln is not None:
            order.append((ln, p['kind'] == 'BoolProperty'))
    # lines not covered by props (pads etc.) keep position via names already in order list
    seen = set()
    packed = {}
    for ln, is_bool in order:
        if ln in seen:
            continue
        seen.add(ln)
        if (is_bool and not prev_bool) or (not is_bool and after_run):
            if 'GCC_PACK' not in ln:
                packed[ln] = ln[:-1] + ' GCC_PACK(4);' if ln.endswith(';') else ln
        after_run = is_bool
        prev_bool = is_bool
    for ln in lines:
        out.append(packed.get(ln, ln))
    return out

def conform_block(bl, shk_rec, res, warnings):
    """Build new member declaration lines for one class/struct block.
    Returns (shrek_lines, orig_lines, changed) or None if no change needed."""
    props = shk_rec['props']
    orig = bl['members']
    orig_by_name = {}
    for md in orig:
        orig_by_name.setdefault(md['name'].lower(), md)
    used = set()
    out = []
    for p in props:
        md = orig_by_name.get(p['name'].lower())
        if md is not None and id(md) not in used:
            used.add(id(md))
            ss = SCRIPT_SIZE.get(p['kind'])
            cs = None if md['bitfield'] else cpp_type_size(md['type'])
            dim = p.get('arraydim') or 1
            if p['kind'] == 'FixedArrayProperty':
                inner_ss = SCRIPT_SIZE.get((p.get('inner') or {}).get('kind'), 0)
                ss = inner_ss * (p.get('count') or 1) if inner_ss else None
            elif dim > 1 and ss:
                ss *= dim
            typ, arr = md['type'], md['array']
            if md['type'].startswith('TArray') and p['kind'] != 'ArrayProperty' and ss and ss != 16:
                # script has a scalar/fixed array where C++ has a TArray: use the script type.
                line = res.synth_decl(p)
                if line:
                    warnings.append('%s.%s: %s[%s] vs TArray (using script type)' % (bl['name'], p['name'], p['kind'], dim))
                    out.append(line)
                    continue
            if p['kind'] == 'FixedArrayProperty' and cs == 16 and ss and ss != 16:
                # script has a fixed-size array where C++ has a TArray: use the script type.
                line = res.synth_decl(p)
                if line:
                    warnings.append('%s.%s: fixed array vs TArray (using script type)' % (bl['name'], p['name']))
                    out.append(line)
                    continue
            elif not arr and dim > 1 and p['kind'] != 'ArrayProperty' and not md['bitfield']:
                # script has a static array where C++ has the scalar: keep the scalar
                # (native code uses element 0) and pad to the script footprint.
                out.append('    %s %s;' % (typ, md['name']))
                out.append('    %s %s__pad[%i];' % (typ, md['name'], dim - 1))
                warnings.append('%s.%s: script array dim %i vs scalar (scalar+pad)' % (bl['name'], p['name'], dim))
                continue
            elif cs and ss and not md['array'] and dim == 1 and cs < ss and cs <= 2 and ss == 4 and p['kind'] != 'BoolProperty':
                # script scalar is 4 bytes where C++ narrowed it: INT keeps offsets right.
                typ = 'INT'
                warnings.append('%s.%s: script size 4 vs C++ %s (using INT)' % (bl['name'], p['name'], md['type']))
            elif cs and ss and not md['array'] and cs != ss and p['kind'] != 'BoolProperty':
                warnings.append('%s.%s: script size %i vs C++ %s size %i (keeping C++)'
                                % (bl['name'], p['name'], ss, md['type'], cs))
            out.append('    %s %s%s%s;' % (typ, md['name'], arr, ':1' if md['bitfield'] else ''))
        else:
            line = res.synth_decl(p)
            if line is None:
                warnings.append('%s.%s: cannot synthesize (%s)' % (bl['name'], p['name'], p['kind']))
                return None
            out.append(line)
    out = gcc_pack_lines(out, props)
    for md in orig:
        if id(md) not in used:
            out.append('    %s %s%s%s;' % (md['type'], md['name'], md['array'], ':1' if md['bitfield'] else ''))
    orig_lines = ['    %s %s%s%s;' % (md['type'], md['name'], md['array'], ':1' if md['bitfield'] else '')
                  if False else md['raw'] for md in orig]
    # identical order+content -> no change
    norm = lambda ls: [re.sub(r'\s+', ' ', re.sub(r'GCC_PACK\(\d+\)', '', l)).strip() for l in ls]
    if norm(out) == norm(orig_lines):
        return None
    return out, orig_lines

def main():
    args = sys.argv[1:]
    apply_ = '--apply' in args
    show = []
    if '--show' in args:
        i = args.index('--show')
        show = args[i + 1:]
        args = args[:i]
    sysdir = args[0]
    shk_classes, shk_structs, dupes = shrek_types(sysdir)
    print('Shrek classes: %i, structs: %i' % (len(shk_classes), len(shk_structs)))
    for d in dupes:
        print('  dupe with differing chains: %s (%s vs %s)' % d)

    hdr_paths = [p for p in glob.glob(os.path.join(REPO, 'Source', '**', '*Classes.h'), recursive=True)
                 if 'Editor' not in p and 'UnrealEd' not in p]
    headers = {}
    for p in hdr_paths:
        headers[p] = parse_header(p)
    blocks_index = {}
    for p, (lines, blocks) in headers.items():
        for bl in blocks:
            blocks_index.setdefault(strip_prefix(bl['name']), (p, bl))

    res = Resolver({p: b for p, (l, b) in headers.items()}, shk_structs)
    warnings, plans = [], {}   # path -> list of (block, shrek_lines, orig_lines)
    matched = missing = 0
    targets = {}
    for name, rec in shk_classes.items():
        targets[name] = rec
    for name, rec in shk_structs.items():
        targets.setdefault(name, rec)
    for name in sorted(targets):
        rec = targets[name]
        hit = blocks_index.get(name)
        if not hit:
            missing += 1
            continue
        matched += 1
        path, bl = hit
        plan = conform_block(bl, rec, res, warnings)
        if plan:
            plans.setdefault(path, []).append((bl, plan[0], plan[1]))
            if name in show or bl['name'] in show:
                print('\n== %s (%s) ==\n#if block:\n%s' % (bl['name'], os.path.basename(path), '\n'.join(plan[0])))
    print('matched header types: %i, no header: %i, to change: %i'
          % (matched, missing, sum(len(v) for v in plans.values())))
    for w in warnings:
        print('WARN:', w)
    if res.synth_structs:
        print('structs to synthesize:', ', '.join(sorted(res.synth_structs)))

    if not apply_:
        print('(dry run; pass --apply to write)')
        return
    for path, items in plans.items():
        lines = headers[path][0]
        # apply per block from bottom to top so line indices stay valid
        items.sort(key=lambda it: it[0]['start'], reverse=True)
        for bl, shrek_lines, orig_lines in items:
            member_set = set(bl['member_idxs'])
            if member_set:
                insert_at = min(member_set)
            else:
                insert_at = bl['body_start']
                # prefer right after a leading 'public:' line
                for j in range(bl['body_start'], bl['end']):
                    if lines[j].strip() == 'public:':
                        insert_at = j + 1
                        break
            new = (['#if UE_SHREK2'] + shrek_lines + ['#else']
                   + [lines[j] for j in bl['member_idxs']] + ['#endif'])
            out_lines = []
            for j, line in enumerate(lines):
                if j == insert_at:
                    out_lines.extend(new)
                if j in member_set:
                    continue
                out_lines.append(line)
            lines = out_lines
        headers[path] = (lines, headers[path][1])
    # synthesized structs go at the end of EngineClasses.h pack region
    if res.synth_structs:
        eng = [p for p in headers if p.endswith('EngineClasses.h')][0]
        lines = headers[eng][0]
        insert_at = None
        for j, line in enumerate(lines):
            if line.startswith('struct ENGINE_API'):
                insert_at = j
                break
        synth = ['#if UE_SHREK2']
        for nm in sorted(res.synth_structs):
            if res.synth_structs[nm]:
                synth.append(res.synth_structs[nm])
        synth.append('#endif')
        if insert_at is not None:
            lines[insert_at:insert_at] = synth
            headers[eng] = (lines, headers[eng][1])
    for path, (lines, _) in headers.items():
        if path in plans or (res.synth_structs and path.endswith('EngineClasses.h')):
            open(path, 'w', encoding='latin-1', newline='\n').write('\n'.join(lines))
            print('wrote', os.path.basename(path))

if __name__ == '__main__':
    main()
