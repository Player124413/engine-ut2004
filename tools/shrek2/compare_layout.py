#!/usr/bin/env python3
# compare_layout.py - compare Shrek 2 script class property order against
# UT2004 generated C++ header member order, per shared native class.
# Usage: compare_layout.py <shrek-System-dir> [class ...]
import re, sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upkg2 import Pkg, PROP_TYPES

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

def parse_headers():
    classes = {}  # name -> dict(members=[(type,name)], super, header)
    for hdr in glob.glob(os.path.join(REPO, 'Source', '**', '*Classes.h'), recursive=True):
        text = open(hdr, encoding='latin-1').read()
        # class blocks: class API Name : public Super { ... DECLARE_CLASS
        for m in re.finditer(r'class\s+\w+_API\s+(\w+)\s*:\s*public\s+(\w+)\s*\{(.*?)\n\};', text, re.S):
            name, sup, body = m.group(1), m.group(2), m.group(3)
            pub = body.split('DECLARE_')[0]
            members = []
            for line in pub.splitlines():
                line = line.strip()
                if not line or line.startswith('//') or line.startswith('public:') or line.startswith('enum'):
                    continue
                line = re.sub(r'//.*', '', line).strip()
                mm = re.match(r'^(.+?)\s+(\w+)\s*(\[[^\]]*\])?\s*(:\s*1)?\s*(GCC_PACK\(\d+\))?\s*;$', line)
                if mm:
                    members.append((mm.group(1).strip(), mm.group(2)))
            classes[name] = dict(members=members, super=sup, header=os.path.basename(hdr))
            if name[0] in 'AU' and len(name) > 1:
                classes.setdefault(name[1:], classes[name])
        for m in re.finditer(r'struct\s+\w+_API\s+(\w+)\s*(?::\s*public\s+(\w+))?\s*\{(.*?)\n\};', text, re.S):
            name, sup, body = m.group(1), m.group(2), m.group(3)
            if name in classes:
                continue
            members = []
            for line in body.splitlines():
                line = re.sub(r'//.*', '', line).strip()
                mm = re.match(r'^(.+?)\s+(\w+)\s*(\[[^\]]*\])?\s*(:\s*1)?\s*(GCC_PACK\(\d+\))?\s*;$', line)
                if mm:
                    members.append((mm.group(1).strip(), mm.group(2)))
            if members:
                classes[name] = dict(members=members, super=sup, header=os.path.basename(hdr))
    return classes

def shrek_classes(sysdir):
    result = {}
    for path in sorted(glob.glob(os.path.join(sysdir, '*.u'))):
        pkg = Pkg(path)
        bod = pkg.bodies()
        for e in pkg.exports:
            if e['class'] not in ('Class', 'Struct'):
                continue
            props = []
            for k in pkg.children_of(bod, e['index']):
                if k['class'] in PROP_TYPES:
                    b = bod.get(k['index'], {})
                    props.append((k['name'], k['class'], b.get('arraydim', 1)))
            result.setdefault(e['name'], dict(pkg=pkg.name, super=e['super'], props=props))
    return result

def main():
    sysdir = sys.argv[1]
    only = sys.argv[2:]
    hdr = parse_headers()
    shk = shrek_classes(sysdir)
    shared = sorted(set(hdr) & set(shk))
    print(f"UT2004 header classes/structs: {len(hdr)}; Shrek script classes/structs: {len(shk)}; shared: {len(shared)}")
    n_ident = n_sub = n_div = 0
    divergent = []
    for name in shared:
        if only and name not in only:
            continue
        hm = [m[1] for m in hdr[name]['members']]
        sp = [p[0] for p in shk[name]['props']]
        # is shrek prop sequence a subsequence of header members (case-insensitive)?
        hml = [x.lower() for x in hm]
        it = iter(hml)
        subseq = all(any(x.lower() == h for h in it) for x in sp)
        missing_in_hdr = [x for x in sp if x.lower() not in hml]
        extra_in_hdr = [x for x in hm if x.lower() not in [y.lower() for y in sp]]
        if sp == hm:
            n_ident += 1; status = 'IDENTICAL'
        elif subseq and not missing_in_hdr:
            n_sub += 1; status = 'subsequence (UT2004 has extra members)'
        else:
            n_div += 1; status = 'DIVERGENT'
            divergent.append(name)
        if only or status != 'IDENTICAL':
            print(f"\n{name} [{shk[name]['pkg']}] super script={shk[name]['super']} hdr={hdr[name]['super']} ({hdr[name]['header']}): {status}")
            if missing_in_hdr:
                print(f"  only in Shrek script: {missing_in_hdr}")
            if extra_in_hdr and status != 'IDENTICAL':
                print(f"  only in UT2004 C++:  {extra_in_hdr[:40]}{'...' if len(extra_in_hdr)>40 else ''}")
            if only:
                print(f"  script props ({len(sp)}): {sp}")
                print(f"  hdr members ({len(hm)}): {hm}")
    print(f"\nidentical={n_ident} subsequence={n_sub} divergent={n_div}")
    if divergent:
        print("divergent classes:", ', '.join(divergent))

if __name__ == '__main__':
    main()
