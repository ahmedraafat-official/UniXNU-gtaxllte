#!/usr/bin/env python3
"""Bind NativeDwMmc to the original IOStorageFamily symbols of the pinned kernel.

IOBlockStorageDevice lives in the prelinked IOStorageFamily kext, so the native
kernel link cannot resolve its constructor, destructor, metaclass, vtable or
inherited methods. The recorded absolute symbols in
scripts/data/native-storage-original-bindings.S were derived from the same
iPadOS 17.7.11 21H461 kernelcache this board pins (f18b4df8...) and already
apply the profile's -0x1000000 collection slide. This recipe re-archives the
driver object with that bindings object.

usage: build-storage-bindings.py [--component NativeDwMmc] [--archive libNativeDwMmc.a]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()  # the UniXNU tree: archives run with cwd=tree
DEVICE = Path(os.environ.get('UNIXNU_DEVICE', ROOT / 'kits/gtaxllte'))
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
import native_prelink as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--component', default='NativeDwMmc')
parser.add_argument('--archive', default='libNativeDwMmc.a')
parser.add_argument('--directory', type=Path, default=ROOT / 'build/native-dwmmc')
parser.add_argument('--subset', action='store_true', default=True,
                    help='bind only the symbols this object imports (the archive may be a second driver)')
args = parser.parse_args()

out = args.directory.resolve()
archive = out / args.archive
obj_path = out / (args.component + '.o')
recorded = DEVICE / 'scripts/data/native-storage-original-bindings.S'
if not obj_path.is_file():
    raise SystemExit(f'{obj_path} not found; build the driver first')
if not recorded.is_file():
    raise SystemExit(f'{recorded} is missing')

data = obj_path.read_bytes()
image = np.macho(data)
offset = next(at for command, at, size in image['commands'] if command == 2)
symoff, count, stroff, _ = struct.unpack_from('<4I', data, offset + 8)
symbols = []
for index in range(count):
    strx, kind, _, _, value = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
    symbols.append((data[stroff + strx:data.index(b'\0', stroff + strx)].decode(), value, kind))
imports = sorted({name for name, value, kind in symbols if kind == 1 and
                  ('IOBlockStorageDevice' in name or 'IOMedia' in name or 'IOStorage' in name)})

values = {}
for line in recorded.read_text().splitlines():
    match = re.match(r'\.set (\S+), (0x[0-9a-f]+)$', line.strip())
    if match:
        values[match.group(1)] = int(match.group(2), 16)
missing = set(imports) - set(values)
if missing:
    raise SystemExit('The recorded bindings do not cover: ' + ', '.join(sorted(missing)))
bindings = {name: values[name] for name in imports}
source = out / 'original-bindings.S'
source.write_text(''.join(f'.weak_definition {name}\n.set {name}, {value:#x}\n'
                          for name, value in sorted(bindings.items())))
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-Werror',
                '-Wno-incompatible-sysroot', '-c', source, '-o', out / 'original-bindings.o'],
               check=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, obj_path, out / 'original-bindings.o'], check=True)
(out / 'bindings.json').write_text(json.dumps(dict(
    component=args.component, archive=archive.name, provenance='scripts/data/native-storage-original-bindings.S',
    compiled_object_sha256=hashlib.sha256(data).hexdigest(),
    bindings={name: hex(value) for name, value in sorted(bindings.items())},
    original_code_modified=False, actual_io_qualified=False), indent=2) + '\n')
print(f'PASS: {args.component} bound to {len(bindings)} original IOStorageFamily symbols')
