#!/usr/bin/env python3
"""Prepare the upstream Mu-Silicium gtaxllte package for UniXNU.

Run by UniXNU as a firmware preflight with UNIXNU_UEFI (the Mu-Silicium
checkout) and UNIXNU_DEVICE (this repository) in the environment.

UEFI patches, each idempotent:

  0001-gtaxllte-unixnu-payload.patch
      adds UniXNU, UniXNUDxe, UniXNURamDiskDxe and DarwinBoot behind
      UNIXNU_PAYLOAD=1 in gtaxllte.dsc and Include/DXE.inc
  0002-gtaxllte-bootshim-image-size.patch
      makes the BootShim ARM64 image header describe the shim plus the
      appended firmware volume, which UniXNU's boot-image verifier requires
  0003-gtaxllte-entry-arg-hob.patch
      saves the bootloader's x0 (the FDT pointer) in a HOB so
      UniXNURamDiskDxe can find /chosen/linux,initrd-start/end and publish
      the payload volume

The edk2 timer libraries are patched in place: S-Boot leaves CNTFRQ_EL0 at
zero, and both the ARM arch timer and the generic timer counter must fall back
to the Exynos 7870's 26 MHz instead of asserting or programming zero ticks.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

PATCHES = (
    ("0001-gtaxllte-unixnu-payload.patch", "Platforms/Samsung/gtaxlltePkg/gtaxllte.dsc", "UNIXNU_PAYLOAD"),
    ("0002-gtaxllte-bootshim-image-size.patch", "BootShim/BootShim.S", "_Payload - _Head + FD_SIZE"),
    ("0003-gtaxllte-entry-arg-hob.patch", "Silicon/Silicium/SiliciumPkg/Sec/Sec.c", "mEntryArgHobGuid"),
)

TIMER_COUNTER_LIBS = (
    "ArmPkg/Library/ArmGenericTimerVirtCounterLib/ArmGenericTimerVirtCounterLib.c",
    "ArmPkg/Library/ArmGenericTimerPhyCounterLib/ArmGenericTimerPhyCounterLib.c",
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


def patch_edk2_timer(uefi: Path) -> int:
    edk2 = uefi / "Mu_Basecore"
    if not edk2.is_dir():
        print("patch-gtaxllte-uefi: Mu_Basecore not found; skipping edk2 timer patches", file=sys.stderr)
        return 0

    timer = edk2 / "ArmPkg/Library/ArmArchTimerLib/ArmArchTimerLib.c"
    if timer.is_file():
        data = timer.read_bytes()
        if b"26000000" not in data:
            pattern = re.compile(
                rb"(  TimerFreq = ArmGenericTimerGetTimerFreq \(\);\r?\n)"
                rb"(\r?\n)"
                rb"(  ASSERT \(TimerFreq != 0\);)"
            )
            replacement = (
                rb"\1\2"
                rb"  if (TimerFreq == 0) {\n"
                rb"    /* S-Boot on the Exynos 7870 leaves CNTFRQ_EL0 unset. */\n"
                rb"    TimerFreq = 26000000;\n"
                rb"  }\n"
                rb"\n"
                rb"\3"
            )
            data, count = pattern.subn(replacement, data, count=1)
            if count:
                timer.write_bytes(data)
                print("patch-gtaxllte-uefi: patched ArmArchTimerLib frequency fallback")
            else:
                print("patch-gtaxllte-uefi: ArmArchTimerLib pattern not found", file=sys.stderr)

    for relative in TIMER_COUNTER_LIBS:
        path = edk2 / relative
        if not path.is_file():
            print(f"patch-gtaxllte-uefi: {path} not found", file=sys.stderr)
            continue
        data = path.read_bytes()
        if b"26000000" in data:
            continue
        pattern = re.compile(rb"\{\r?\n  return ArmReadCntFrq \(\);\r?\n\}")
        replacement = (
            rb"{\r\n"
            rb"  UINTN  Freq;\r\n"
            rb"\r\n"
            rb"  Freq = ArmReadCntFrq ();\r\n"
            rb"  if (Freq == 0) {\r\n"
            rb"    /* S-Boot on the Exynos 7870 leaves CNTFRQ_EL0 unset. */\r\n"
            rb"    Freq = 26000000;\r\n"
            rb"  }\r\n"
            rb"\r\n"
            rb"  return Freq;\r\n"
            rb"}"
        )
        data, count = pattern.subn(replacement, data, count=1)
        if count:
            path.write_bytes(data)
            print(f"patch-gtaxllte-uefi: patched {path.name} frequency fallback")
        else:
            print(f"patch-gtaxllte-uefi: {path.name} pattern not found", file=sys.stderr)

    return 0


def patch_edk2_exception(uefi: Path) -> int:
    """Repeat the fault registers after the stack dump.

    The console scrolls the register block off the screen on a panic; the
    summary after the dump stays visible, so a photographed panic gives the
    ELR, ESR and FAR instead of only the stack.
    """
    path = uefi / "Mu_Basecore/UefiCpuPkg/Library/CpuExceptionHandlerLib/AArch64/CpuExceptionHandlerLib.c"
    if not path.is_file():
        print("patch-gtaxllte-uefi: CpuExceptionHandlerLib.c not found; skipping exception summary", file=sys.stderr)
        return 0
    data = path.read_bytes()
    if b"FAULT SP" in data:
        return 0
    marker = b"  // Attempt to print that we had a synchronous exception to ConOut."
    if marker not in data:
        print("patch-gtaxllte-uefi: exception summary marker not found", file=sys.stderr)
        return 1
    summary = (
        b"  DEBUG ((DEBUG_ERROR, \"\\nFAULT SP 0x%016lx ELR 0x%016lx ESR 0x%08lx FAR 0x%016lx\\n\",\n"
        b"          SystemContext.SystemContextAArch64->SP,\n"
        b"          SystemContext.SystemContextAArch64->ELR,\n"
        b"          SystemContext.SystemContextAArch64->ESR,\n"
        b"          SystemContext.SystemContextAArch64->FAR));\n\n"
    )
    path.write_bytes(data.replace(marker, summary + marker, 1))
    print("patch-gtaxllte-uefi: added the exception fault summary")
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

    status = patch_edk2_timer(uefi)
    if status != 0:
        return status
    return patch_edk2_exception(uefi)


if __name__ == "__main__":
    raise SystemExit(main())
