#!/usr/bin/env python3
"""Add UniXNU, UniXNUDxe, UniXNURamDiskDxe and DarwinBoot to the upstream
Mu-Silicium gtaxllte package.

Run by UniXNU as a firmware preflight with UNIXNU_UEFI (the Mu-Silicium
checkout) and UNIXNU_DEVICE (this repository) in the environment. The patch is
idempotent: a checkout that already contains UNIXNU_PAYLOAD is left alone.
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    uefi = os.environ.get("UNIXNU_UEFI")
    device = os.environ.get("UNIXNU_DEVICE")
    if not uefi or not device:
        print("patch-gtaxllte-uefi: UNIXNU_UEFI and UNIXNU_DEVICE must be set", file=sys.stderr)
        return 2

    uefi = Path(uefi).resolve()
    dsc = uefi / "Platforms/Samsung/gtaxlltePkg/gtaxllte.dsc"
    patch = Path(device).resolve() / "patches/uefi/0001-gtaxllte-unixnu-payload.patch"
    if not dsc.is_file():
        print(f"patch-gtaxllte-uefi: {dsc} not found; is this the gtaxllte checkout?", file=sys.stderr)
        return 2
    if not patch.is_file():
        print(f"patch-gtaxllte-uefi: {patch} not found", file=sys.stderr)
        return 2

    if "UNIXNU_PAYLOAD" in dsc.read_text():
        print("patch-gtaxllte-uefi: already applied")
        return 0

    if (uefi / ".git").exists():
        check = subprocess.run(["git", "-C", str(uefi), "apply", "--check", str(patch)])
        if check.returncode == 0:
            subprocess.run(["git", "-C", str(uefi), "apply", str(patch)], check=True)
            print("patch-gtaxllte-uefi: applied with git")
            return 0
        print("patch-gtaxllte-uefi: git apply --check failed; trying patch(1)", file=sys.stderr)

    with patch.open("rb") as stream:
        result = subprocess.run(["patch", "-p1", "-d", str(uefi)], stdin=stream)
    if result.returncode != 0:
        print("patch-gtaxllte-uefi: patch(1) failed; the upstream package changed", file=sys.stderr)
        return result.returncode

    print("patch-gtaxllte-uefi: applied with patch(1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
