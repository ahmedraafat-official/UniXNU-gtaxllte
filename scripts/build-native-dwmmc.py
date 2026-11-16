#!/usr/bin/env python3
"""Build NativeDwMmc against the actual source-kernel ABI.

The compile recipe is taken from the XNU build's IOPlatformExpert.cpo.json,
the same way scripts/build-arm-driver.py builds the catalog drivers. This
device script exists because the driver is not in the UniXNU component
catalog yet.
"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path.cwd()  # the UniXNU checkout: the build driver runs archives with cwd=tree
DEVICE = Path(os.environ.get('UNIXNU_DEVICE', ROOT / 'kits/gtaxllte'))
SOURCE = DEVICE / 'package/NativeDwMmc.cpp'
OUT = ROOT / 'build/native-dwmmc'
RECORD = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'

OUT.mkdir(parents=True, exist_ok=True)
record = json.loads(RECORD.read_text().strip().rstrip(','))
destination = OUT / 'NativeDwMmc.o'
command = []
arguments = record['arguments']
index = 0
while index < len(arguments):
    value = arguments[index]
    if value == record['file']:
        command.append(str(SOURCE))
    elif value == '-o':
        command.extend(['-o', str(destination)])
        index += 1
    elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
        index += 1
    elif not value.startswith('-W'):
        command.append(value)
    index += 1
command.extend([
    '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
    '-Wno-c99-designator', '-Wno-reorder-init-list',
    '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier',
    '-I', str(ROOT / '.cache/xnu-work/osfmk'),
    '-I', str(ROOT / 'build/storage-abi/include'),
])
(OUT / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
result = subprocess.run(command, cwd=record['directory'], stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
(OUT / 'compile.log').write_text(result.stdout)
if result.returncode:
    raise SystemExit(result.stdout[-3500:])
magic = destination.read_bytes()[:8]
if magic != b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01':
    raise SystemExit(f'NativeDwMmc.o is not an arm64 Mach-O object: {magic.hex()}')
archive = OUT / 'libNativeDwMmc.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, destination], check=True)
print('NativeDwMmc: ABI build and archive written')
