#!/usr/bin/env python3
"""Prepare the upstream Mu-Silicium gtaxllte package for UniXNU.

Run by UniXNU as a firmware preflight with UNIXNU_UEFI (the Mu-Silicium
checkout) and UNIXNU_DEVICE (this repository) in the environment.

Two patches, each idempotent:

  0001-gtaxllte-unixnu-payload.patch
      adds UniXNU, UniXNUDxe, UniXNURamDiskDxe and DarwinBoot behind
      UNIXNU_PAYLOAD=1 in gtaxllte.dsc and Include/DXE.inc
  0002-gtaxllte-bootshim-image-size.patch
      makes the BootShim ARM64 image header describe the shim plus the
      appended firmware volume, which UniXNU's boot-image verifier requires
"""

import os
import subprocess
import sys
from pathlib import Path

PATCHES = (
    ("0001-gtaxllte-unixnu-payload.patch", "Platforms/Samsung/gtaxlltePkg/gtaxllte.dsc", "UNIXNU_PAYLOAD"),
    ("0002-gtaxllte-bootshim-image-size.patch", "BootShim/BootShim.S", "_Payload - _Head + FD_SIZE"),
    ("0003-gtaxllte-bootshim-cntfrq.patch", "BootShim/BootShim.S", "cntfrq_el0"),
)


def apply(uefi: Path, patch: Path) -> int:
    if (uefi / ".git").exists():
        check = subprocess.run(["git", "-C", str(uefi), "apply", "--check", str(patch)])
        if check.returncode == 0:
            subprocess.run(["git", "-C", str(uefi), "apply", str(patch)], check=True)
            print(f"patch-gtaxllte-uefi: applied {patch.name} with git")
            return 0
        print(f"patch-gtaxllte-uefi: git apply --check failed for {patch.name}; trying patch(1)", file=sys.stderr)

    with patch.open("rb") as stream:
        result = subprocess.run(["patch", "-p1", "-d", str(uefi)], stdin=stream)
    if result.returncode != 0:
        print(f"patch-gtaxllte-uefi: {patch.name} failed; the upstream package changed", file=sys.stderr)
        return result.returncode
    print(f"patch-gtaxllte-uefi: applied {patch.name} with patch(1)")
    return 0


def main() -> int:
    uefi_env = os.environ.get("UNIXNU_UEFI")
    device_env = os.environ.get("UNIXNU_DEVICE")
    if not uefi_env or not device_env:
        print("patch-gtaxllte-uefi: UNIXNU_UEFI and UNIXNU_DEVICE must be set", file=sys.stderr)
        return 2

    uefi = Path(uefi_env).resolve()
    device = Path(device_env).resolve()
    for name, target, marker in PATCHES:
        path = device / "patches/uefi" / name
        file = uefi / target
        if not file.is_file():
            print(f"patch-gtaxllte-uefi: {file} not found; is this the gtaxllte checkout?", file=sys.stderr)
            return 2
        if not path.is_file():
            print(f"patch-gtaxllte-uefi: {path} not found", file=sys.stderr)
            return 2
        if marker in file.read_text():
            print(f"patch-gtaxllte-uefi: {name} already applied")
            continue
        result = apply(uefi, path)
        if result != 0:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
