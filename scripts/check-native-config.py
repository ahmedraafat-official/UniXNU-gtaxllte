#!/usr/bin/env python3
"""Reject native-kernel configs that still assume the prelinked kext collection.

The gtaxllte images are packed with --skip-prelink: the kernel is loaded at its
own link address and the target's kernelcache is never mapped. Three settings
in the shared ARM64 profile assume that collection and must be overridden by
the device defconfig:

  * SoftwareAES subclasses IOAESAccelerator from the collection;
  * CONFIG_XNU_NATIVE_AES makes the build demand that archive;
  * CONFIG_XNU_CRYPTO_BOOTSTRAP="prelinked" omits the source corecrypto
    provider and expects the original crypto kext to fill g_crypto_funcs.

Each one has already produced a boot panic. Check them before a 40-minute
build and a flash cycle.
"""
import argparse
import re
import sys
from pathlib import Path

REQUIRED = {
    'CONFIG_XNU_CRYPTO_BOOTSTRAP': 'prelinked',
    'CONFIG_XNU_NATIVE_AES': 'n',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('defconfig', type=Path)
    parser.add_argument('--board', type=Path, default=None,
                        help='board.toml to check for SoftwareAES (optional)')
    args = parser.parse_args()

    text = args.defconfig.read_text()
    values = {}
    for line in text.splitlines():
        match = re.match(r'\s*(CONFIG_XNU_[A-Z0-9_]+)\s*=\s*(.*?)\s*$', line)
        if match:
            values[match.group(1)] = match.group(2).strip('"')

    errors = []
    for key, wanted in REQUIRED.items():
        seen = values.get(key)
        if seen != wanted:
            errors.append(f'{key} is {seen!r}; a --skip-prelink kernel needs {wanted!r}')

    if args.board:
        board = args.board.read_text()
        include = re.search(r'^include\s*=\s*\[(.*?)\]', board, re.M | re.S)
        if include and 'SoftwareAES' in include.group(1):
            errors.append('[components] include still selects SoftwareAES')

    # In this native port "prelinked" means "leave the source provider
    # unregistered"; the kernel must then carry the NULL-safe kmem wrapper.
    series = args.defconfig.resolve().parent.parent / 'patches/xnu/series'
    if values.get('CONFIG_XNU_CRYPTO_BOOTSTRAP') == 'prelinked':
        if not series.is_file() or 'zzz-kmem-no-crypto.patch' not in series.read_text():
            errors.append('prelinked crypto mode needs zzz-kmem-no-crypto.patch in patches/xnu/series')

    if errors:
        print(f'{args.defconfig}: native-kernel assumptions:')
        for error in errors:
            print(f'  - {error}')
        print('The target kernelcache is not mapped; fix the defconfig or map the collection.')
        return 1

    print(f'{args.defconfig}: native-kernel config is consistent')
    return 0


if __name__ == '__main__':
    sys.exit(main())
