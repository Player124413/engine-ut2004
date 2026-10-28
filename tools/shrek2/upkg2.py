#!/usr/bin/env python3
# upkg2.py - UE2 package inspector for the Shrek 2 port.
# Parses the name/import/export tables plus the UField family export bodies
# (classes, structs, states, functions, properties, enums, consts), enough to
# diff another UE2 game's script surface against this engine's native one.
# Usage: upkg2.py dump <pkg.u>...        - classes/functions/properties report
#        upkg2.py check <pkg.u>...       - validate parse vs SerialSize
import struct, sys, os, json

class R:
    def __init__(self, d):
        self.d = d; self.p = 0
    def seek(self, p): self.p = p
    def tell(self): return self.p
    def bytes(self, n):
        v = self.d[self.p:self.p+n]; self.p += n; return v
    def u8(self): v = self.d[self.p]; self.p += 1; return v
    def u16(self): v = struct.unpack_from('<H', self.d, self.p)[0]; self.p += 2; return v
    def i32(self): v = struct.unpack_from('<i', self.d, self.p)[0]; self.p += 4; return v
    def u32(self): v = struct.unpack_from('<I', self.d, self.p)[0]; self.p += 4; return v
    def u64(self): v = struct.unpack_from('<Q', self.d, self.p)[0]; self.p += 8; return v
    def idx(self):  # AR_INDEX compact index
        b0 = self.d[self.p]; self.p += 1
        sign = b0 & 0x80
        v = b0 & 0x3f
        if b0 & 0x40:
            b = self.d[self.p]; self.p += 1; v |= (b & 0x7f) << 6
            if b & 0x80:
                b = self.d[self.p]; self.p += 1; v |= (b & 0x7f) << 13
                if b & 0x80:
                    b = self.d[self.p]; self.p += 1; v |= (b & 0x7f) << 20
                    if b & 0x80:
                        b = self.d[self.p]; self.p += 1; v |= b << 27
        return -v if sign else v
    def fstr(self):
        n = self.idx()
        if n == 0: return ""
        if n > 0:
            s = self.bytes(n)
            return s.rstrip(b'\0').decode('latin-1')
        s = self.bytes(-n * 2)
        return s.decode('utf-16-le').rstrip('\0')

EX = dict(
    LocalVariable=0x00, InstanceVariable=0x01, DefaultVariable=0x02, Return=0x04,
    Switch=0x05, Jump=0x06, JumpIfNot=0x07, Stop=0x08, Assert=0x09, Case=0x0A,
    Nothing=0x0B, LabelTable=0x0C, GotoLabel=0x0D, EatString=0x0E, Let=0x0F,
    DynArrayElement=0x10, New=0x11, ClassContext=0x12, MetaCast=0x13, LetBool=0x14,
    LineNumber=0x15, EndFunctionParms=0x16, Self=0x17, Skip=0x18, Context=0x19,
    ArrayElement=0x1A, VirtualFunction=0x1B, FinalFunction=0x1C, IntConst=0x1D,
    FloatConst=0x1E, StringConst=0x1F, ObjectConst=0x20, NameConst=0x21,
    RotationConst=0x22, VectorConst=0x23, ByteConst=0x24, IntZero=0x25, IntOne=0x26,
    True_=0x27, False_=0x28, NativeParm=0x29, NoObject=0x2A, IntConstByte=0x2C,
    BoolVariable=0x2D, DynamicCast=0x2E, Iterator=0x2F, IteratorPop=0x30,
    IteratorNext=0x31, StructCmpEq=0x32, StructCmpNe=0x33, UnicodeStringConst=0x34,
    RangeConst=0x35, StructMember=0x36, DynArrayLength=0x37, GlobalFunction=0x38,
    PrimitiveCast=0x39, DynArrayInsert=0x40, DynArrayRemove=0x41, DebugInfo=0x42,
    DelegateFunction=0x43, DelegateProperty=0x44, LetDelegate=0x45, PointerConst=0x46,
    EndOfScript=0x47, ExtendedNative=0x60, FirstNative=0x70)

CPF_Parm = 0x00000080
CPF_OutParm = 0x00000100
CPF_Net = 0x00000020
CPF_ReturnParm = 0x00000400
CPF_CommentString = 0x01000000  # verified against parse; see note in report
FUNC_Native = 0x00000400
FUNC_Net = 0x00000040

class Pkg:
    def __init__(self, path, struct_flags=None):
        """struct_flags: None=auto (try licensee rule), True/False forces the
        UStruct StructFlags dword that UT2004 gates on LicenseeVer>=0x1A."""
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        d = open(path, 'rb').read()
        self.data = d
        r = R(d)
        self.r = r
        tag = r.u32(); assert tag == 0x9E2A83C1, hex(tag)
        ver = r.u32(); self.ver = ver & 0xffff; self.lic = (ver >> 16) & 0xffff
        self.pkgflags = r.u32()
        nc, no = r.i32(), r.i32(); ec, eo = r.i32(), r.i32(); ic, io = r.i32(), r.i32()
        self.struct_flags = (self.lic >= 0x1A) if struct_flags is None else struct_flags
        r.seek(no); self.names = []
        for _ in range(nc):
            s = r.fstr(); r.u32(); self.names.append(s)
        r.seek(io); self.imports = []
        for _ in range(ic):
            cp = r.idx(); cn = r.idx(); pi = r.i32(); on = r.idx()
            self.imports.append(dict(cp=self.names[cp], cn=self.names[cn], pi=pi, name=self.names[on]))
        r.seek(eo); self.exports = []
        for _ in range(ec):
            ci = r.idx(); si = r.idx(); pi = r.i32(); on = r.idx(); of = r.u32(); ss = r.idx()
            so = r.idx() if ss else 0
            self.exports.append(dict(ci=ci, si=si, pi=pi, name=self.names[on], flags=of, size=ss, off=so))
        for i, e in enumerate(self.exports):
            e['class'] = self.objname(e['ci']) if e['ci'] != 0 else 'Class'
            e['super'] = self.objname(e['si']) if e['si'] != 0 else None
            e['outer'] = self.outername(e['pi'])
            e['index'] = i + 1

    def objname(self, idx):
        if idx == 0: return None
        if idx > 0: return self.exports[idx-1]['name']
        return self.imports[-idx-1]['name']

    def outername(self, pi):
        if pi == 0: return self.name
        if pi < 0:
            im = self.imports[-pi-1]
            return self.outername(im['pi']) + '.' + im['name']
        e = self.exports[pi-1]
        return self.outername(e['pi']) + '.' + e['name']

    # ---- export body parsing (UField family) ----
    def _skip_tagged(self, r):
        # FPropertyTag stream; ends with NAME_None (name index 0).
        while True:
            ni = r.idx()
            if ni == 0: return
            info = r.u8()
            typ = info & 0x0f
            if typ == 5:  # NAME_StructProperty id in tag space is by EName? struct tag carries ItemName
                pass
            # struct properties have an extra ItemName when type==StructProperty;
            # tag Type is an EName index of the property class name, not small enum:
            # in practice field objects carry an empty stream, so anything here is an error.
            raise ValueError('non-empty tagged stream in field object')

    # Script bytecode walk (UStruct::SerializeExpr): file-side operand sizes.
    # FName/object operands are one AR_INDEX in the file but 4 bytes in memory.
    def _expr(self, r, depth=0):
        if depth > 200:
            raise ValueError('expr too deep')
        tok = r.u8(); mem = 1
        E = EX
        def name_or_ptr():
            nonlocal mem
            r.idx(); mem += 4
        def parms():
            nonlocal mem
            while True:
                t, m = self._expr(r, depth + 1)
                mem += m
                if t == E['EndFunctionParms']:
                    break
            # HANDLE_OPTIONAL_DEBUG_INFO
            save = r.tell()
            try:
                b = r.u8()
                if b == E['DebugInfo']:
                    ver = r.i32()
                    if ver == 100:
                        r.seek(save)
                        t2, m2 = self._expr(r, depth + 1)
                        mem += m2
                        return
                r.seek(save)
            except Exception:
                r.seek(save)
        if tok >= E['FirstNative']:
            parms()
        elif tok >= E['ExtendedNative']:
            r.u8(); mem += 1
            parms()
        elif tok == E['PrimitiveCast']:
            r.u8(); mem += 1; t, m = self._expr(r, depth + 1); mem += m
        elif tok in (E['Let'], E['LetBool'], E['LetDelegate'], E['ArrayElement'], E['DynArrayElement']):
            for _ in range(2):
                t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['Jump']:
            r.u16(); mem += 2
        elif tok in (E['LocalVariable'], E['InstanceVariable'], E['DefaultVariable'],
                     E['NativeParm'], E['ObjectConst']):
            name_or_ptr()
        elif tok == E['DebugInfo']:
            r.i32(); r.i32(); r.i32(); r.u8(); mem += 13
        elif tok in (E['BoolVariable'], E['Nothing'], E['EndOfScript'], E['EndFunctionParms'],
                     E['IntZero'], E['IntOne'], E['True_'], E['False_'], E['NoObject'],
                     E['Self'], E['IteratorPop'], E['Stop'], E['IteratorNext'], E['LineNumber']):
            if tok == E['LineNumber']:
                raise ValueError('EX_LineNumber unhandled')
        elif tok in (E['EatString'], E['Return'], E['GotoLabel'], E['DynArrayLength']):
            t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['FinalFunction']:
            name_or_ptr(); parms()
        elif tok in (E['VirtualFunction'], E['GlobalFunction']):
            name_or_ptr(); parms()
        elif tok == E['DelegateFunction']:
            name_or_ptr(); name_or_ptr()
        elif tok in (E['ClassContext'], E['Context']):
            t, m = self._expr(r, depth + 1); mem += m
            r.u16(); r.u8(); mem += 3
            t, m = self._expr(r, depth + 1); mem += m
        elif tok in (E['DynArrayInsert'], E['DynArrayRemove']):
            for _ in range(3):
                t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['New']:
            for _ in range(4):
                t, m = self._expr(r, depth + 1); mem += m
        elif tok in (E['IntConst'], E['FloatConst'], E['PointerConst']):
            r.i32(); mem += 4
        elif tok == E['StringConst']:
            while True:
                c = r.u8(); mem += 1
                if c == 0: break
        elif tok == E['UnicodeStringConst']:
            while True:
                c = r.u16(); mem += 2
                if c == 0: break
        elif tok == E['NameConst']:
            name_or_ptr()
        elif tok in (E['RotationConst'], E['VectorConst'], E['RangeConst']):
            for _ in range(3):
                r.i32(); mem += 4
        elif tok in (E['ByteConst'], E['IntConstByte']):
            r.u8(); mem += 1
        elif tok in (E['MetaCast'], E['DynamicCast'], E['StructMember']):
            name_or_ptr()
            t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['JumpIfNot']:
            r.u16(); mem += 2
            t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['Iterator']:
            t, m = self._expr(r, depth + 1); mem += m
            r.u16(); mem += 2
        elif tok == E['Switch']:
            r.u8(); mem += 1
            t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['Assert']:
            r.u16(); mem += 2
            t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['Case']:
            w = r.u16(); mem += 2
            if w != 0xffff:
                t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['LabelTable']:
            while True:
                ni = r.idx(); r.i32(); mem += 8
                if ni == 0: break
        elif tok == E['Skip']:
            r.u16(); mem += 2
            t, m = self._expr(r, depth + 1); mem += m
        elif tok in (E['StructCmpEq'], E['StructCmpNe']):
            name_or_ptr()
            for _ in range(2):
                t, m = self._expr(r, depth + 1); mem += m
        elif tok == E['DelegateProperty']:
            name_or_ptr()
        else:
            raise ValueError(f'bad expr token 0x{tok:02x}')
        return tok, mem

    def _field_head(self, r):
        # UObject part: no stack for fields; tagged props of the metaclass (empty in practice),
        # except UClass objects themselves where it is skipped entirely.
        return r.idx(), r.idx()  # SuperField, Next

    def parse_export(self, e):
        """Returns dict with parsed body for UField-family exports, else None."""
        cls = e['class']
        r = R(self.data)
        r.seek(e['off'])
        end = e['off'] + e['size']
        out = {}
        is_class_obj = (cls == 'Class')
        # UObject::Serialize tagged stream (skipped for class objects)
        if not is_class_obj:
            save = r.tell()
            try:
                self._skip_tagged(r)
            except Exception:
                r.seek(save)  # maybe stream absent; fall through and let validation judge
        body_start = r.tell()
        superfield, nxt = r.idx(), r.idx()
        out['superfield'] = self.objname(superfield)
        out['next'] = nxt
        def is_a(*names): return cls in names
        if is_a('Const'):
            out['value'] = r.fstr()
        elif is_a('Enum'):
            n = r.idx()
            out['names'] = [self.names[r.idx()] for _ in range(n)]
        elif is_a('ByteProperty', 'IntProperty', 'BoolProperty', 'FloatProperty', 'NameProperty',
                  'StrProperty', 'ObjectProperty', 'ClassProperty', 'ArrayProperty',
                  'StructProperty', 'DelegateProperty', 'MapProperty', 'FixedArrayProperty',
                  'PointerProperty'):
            out['arraydim'] = r.i32()
            out['propflags'] = r.u32()
            out['category'] = self.names[r.idx()]
            if out['propflags'] & CPF_Net:
                out['repoffset'] = r.u16()
            if out['propflags'] & CPF_CommentString:
                r.fstr()
            if cls == 'ByteProperty':
                out['enum'] = self.objname(r.idx())
            elif cls == 'ObjectProperty':
                out['propclass'] = self.objname(r.idx())
            elif cls == 'ClassProperty':
                out['propclass'] = self.objname(r.idx())
                out['metaclass'] = self.objname(r.idx())
            elif cls == 'ArrayProperty':
                out['inner'] = r.idx()
            elif cls == 'StructProperty':
                out['struct'] = self.objname(r.idx())
            elif cls == 'DelegateProperty':
                out['func'] = self.objname(r.idx())
            elif cls == 'MapProperty':
                out['key'] = r.idx(); out['value'] = r.idx()
            elif cls == 'FixedArrayProperty':
                out['inner'] = r.idx(); out['count'] = r.i32()
        elif is_a('Struct', 'Function', 'State', 'Class'):
            out['scripttext'] = r.idx()
            out['children'] = r.idx()
            out['friendly'] = self.names[r.idx()]
            if self.struct_flags:
                out['structflags'] = r.u32()
            elif self.lic == 0 and self.ver >= 129:
                out['cpptext'] = r.idx()  # UE2 mainline: CppText objref after FriendlyName (absent in UT2004)
            out['line'] = r.i32(); out['textpos'] = r.i32()
            ssize = r.i32()
            out['scriptsize'] = ssize
            icode = 0
            while icode < ssize:
                _t, m = self._expr(r)
                icode += m
            out['script_icode_ok'] = (icode == ssize)
            if is_a('Function'):
                out['inative'] = r.u16()
                out['operprecedence'] = r.u8()
                out['funcflags'] = r.u32()
                if out['funcflags'] & FUNC_Net:
                    out['repoffset'] = r.u16()
            elif is_a('State', 'Class'):
                r.u64(); r.u64()  # ProbeMask, IgnoreMask
                r.u16(); r.u32()  # LabelTableOffset, StateFlags
                if is_a('Class'):
                    out['classflags'] = r.u32()
                    r.bytes(16)  # ClassGuid
                    ndep = r.idx()
                    for _ in range(ndep):
                        r.idx(); r.u32(); r.u32()
                    nimp = r.idx()
                    for _ in range(nimp):
                        r.idx()
                    out['classwithin'] = self.objname(r.idx())
                    out['configname'] = self.names[r.idx()]
                    if self.ver >= 99:
                        nh = r.idx()
                        for _ in range(nh):
                            r.idx()
                    r.seek(end)  # Defaults tagged stream: skip
        else:
            return None
        out['parsed_end'] = r.tell()
        out['expected_end'] = end
        out['ok'] = (r.tell() == end)
        return out

    def bodies(self):
        bod = {}
        for e in self.exports:
            try:
                b = self.parse_export(e)
            except Exception as ex:
                b = {'error': str(ex)}
            if b is not None:
                bod[e['index']] = b
        return bod

    def children_of(self, bod, struct_idx):
        """Exports (in declaration order) whose outer is the given struct export,
        ordered by the Children/Next chain when available."""
        FIELD_CLASSES = {'Const','Enum','Function','State','Struct','Class'} | {'ByteProperty','IntProperty','BoolProperty','FloatProperty','NameProperty','StrProperty','ObjectProperty','ClassProperty','ArrayProperty','StructProperty','DelegateProperty','MapProperty','FixedArrayProperty','PointerProperty'}
        kids = [e for e in self.exports if e['pi'] == struct_idx and e['class'] in FIELD_CLASSES]
        first = bod.get(struct_idx, {}).get('children')
        if first and first > 0:
            order = []
            cur = first
            seen = set()
            while cur and cur > 0 and cur not in seen and cur <= len(self.exports):
                seen.add(cur)
                order.append(self.exports[cur-1])
                cur = bod.get(cur, {}).get('next', 0)
            if len(order) == len(kids):
                return order
        return kids

def cmd_check(paths):
    for p in paths:
        for sf in (None,):
            pkg = Pkg(p, sf)
            bod = pkg.bodies()
            ok = sum(1 for b in bod.values() if b.get('ok'))
            bad = [(i, b) for i, b in bod.items() if not b.get('ok')]
            print(f"{pkg.name}: ver={pkg.ver} lic={pkg.lic} structflags={pkg.struct_flags} "
                  f"field-exports={len(bod)} ok={ok} bad={len(bad)}")
            for i, b in bad[:8]:
                e = pkg.exports[i-1]
                print(f"   #{i} {e['class']} {e['outer']}.{e['name']} size={e['size']} -> {b}")

PROP_TYPES = {'ByteProperty': 'byte', 'IntProperty': 'int', 'BoolProperty': 'bool',
              'FloatProperty': 'float', 'NameProperty': 'name', 'StrProperty': 'string',
              'ObjectProperty': 'object', 'ClassProperty': 'class', 'ArrayProperty': 'array',
              'StructProperty': 'struct', 'DelegateProperty': 'delegate', 'MapProperty': 'map',
              'FixedArrayProperty': 'fixedarray', 'PointerProperty': 'pointer'}

def prop_desc(pkg, bod, e):
    b = bod.get(e['index'], {})
    t = PROP_TYPES.get(e['class'], e['class'])
    if e['class'] == 'ObjectProperty': t = b.get('propclass') or t
    if e['class'] == 'ClassProperty': t = 'class<%s>' % (b.get('metaclass') or '?')
    if e['class'] == 'StructProperty': t = b.get('struct') or t
    if e['class'] == 'ByteProperty' and b.get('enum'): t = b['enum']
    if e['class'] == 'ArrayProperty':
        inner = b.get('inner')
        if inner and inner > 0:
            ie = pkg.exports[inner-1]
            t = 'array<%s>' % prop_desc(pkg, bod, ie)[0]
    dim = b.get('arraydim', 1)
    if dim != 1: t += f'[{dim}]'
    return t, b.get('propflags', 0)

def cmd_dump(paths):
    for p in paths:
        pkg = Pkg(p)
        bod = pkg.bodies()
        print(f"\n## {pkg.name} (ver {pkg.ver}, lic {pkg.lic})")
        for e in pkg.exports:
            if e['class'] != 'Class': continue
            b = bod.get(e['index'], {})
            print(f"\nclass {e['name']} extends {e['super']} flags=0x{b.get('classflags',0):x} objflags=0x{e['flags']:x}")
            for k in pkg.children_of(bod, e['index']):
                kb = bod.get(k['index'], {})
                if k['class'] in PROP_TYPES:
                    t, pf = prop_desc(pkg, bod, k)
                    print(f"  var {t} {k['name']} pf=0x{pf:x}")
                elif k['class'] == 'Function':
                    ff = kb.get('funcflags', 0)
                    parms = []
                    for pe in pkg.children_of(bod, k['index']):
                        if pe['class'] in PROP_TYPES:
                            t, pf = prop_desc(pkg, bod, pe)
                            tag = 'ret ' if pf & CPF_ReturnParm else ('out ' if pf & CPF_OutParm else '')
                            parms.append(f"{tag}{t} {pe['name']}")
                    nat = f" native({kb.get('inative',0)})" if ff & FUNC_Native else ""
                    print(f"  func{nat} {k['name']}({', '.join(parms)}) ff=0x{ff:x}")
                elif k['class'] in ('Struct', 'Enum', 'State'):
                    print(f"  {k['class'].lower()} {k['name']}")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    {'check': cmd_check, 'dump': cmd_dump}[sys.argv[1]](sys.argv[2:])
