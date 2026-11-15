#!/usr/bin/env python3
"""Reject a native kernel that still references the prelinked kext collection.

A kernel packed with --skip-prelink is loaded at its own link address and does
not map the target's kernelcache. The target fileset contributes some classes
to the source tree as *absolute* symbols at their prelinked addresses (for
example IOAESAccelerator, whose metaClass sits at 0xfffffff006ae52d0). Calling
such a class, or constructing a subclass of it, faults in the physmap gap that
the native kernel never populates.

The linker records those imports as external absolute symbols. Any external
absolute symbol between the physmap base and the kernel's own __TEXT address is
therefore a flash-time panic, not a link error. Fail the build instead.
"""
import argparse
import re
import subprocess
import sys

PHYSMAP_BASE = 0xfffffff000000000
SYMBOL = re.compile(r'^([0-9a-fA-F]{16}) \(absolute\) external (\S+)$')
TEXT_SEGMENT = re.compile(r'segname __TEXT\n\s+vmaddr (0x[0-9a-fA-F]+)')


def kernel_text_base(kernel):
    out = subprocess.run(['otool', '-l', str(kernel)], capture_output=True,
                         text=True, check=True).stdout
    match = TEXT_SEGMENT.search(out)
    if not match:
        raise SystemExit(f'{kernel}: no __TEXT segment')
    return int(match.group(1), 16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=str)
    args = parser.parse_args()

    base = kernel_text_base(args.kernel)
    if not PHYSMAP_BASE < base <= 0xffffffffffffffff:
        raise SystemExit(f'{args.kernel}: implausible __TEXT address 0x{base:016x}')

    out = subprocess.run(['nm', '-m', args.kernel], capture_output=True,
                         text=True, check=True).stdout
    bad = []
    for line in out.splitlines():
        match = SYMBOL.match(line.strip())
        if not match:
            continue
        address = int(match.group(1), 16)
        if PHYSMAP_BASE <= address < base:
            bad.append((address, match.group(2)))

    if bad:
        print(f'{args.kernel}: {len(bad)} external absolute symbol(s) in prelinked '
              f'kext space 0x{PHYSMAP_BASE:016x}..0x{base:016x}:')
        for address, name in bad[:32]:
            print(f'  0x{address:016x}  {name}')
        if len(bad) > 32:
            print(f'  ... {len(bad) - 32} more')
        print('A --skip-prelink kernel cannot map the prelinked collection. '
              'Drop the source component that imports it, or map the collection.')
        return 1

    print(f'{args.kernel}: no prelinked kext references (__TEXT 0x{base:016x})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
