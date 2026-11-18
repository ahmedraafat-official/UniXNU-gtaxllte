# Display bring-up plan (gtaxllte)

The root is mounted and `/sbin/launchd` is handed off, but the panel still
shows the UEFI menu because there is no display driver. The shortest path is
the **GopFramebuffer legacy backend** with QuartzCore's CPU renderer, the same
path pipa uses with its firmware GOP.

## Hardware already known

- Panel: 1200x1920, 32 bpp, 4800-byte rows, GOP framebuffer at `0x67000000`
  (UEFI reserved `0x01194000`; the kernel direct-maps it with
  `ml_io_map_wcomb` for the stage bars and the panic screen).
- No DECON/DSI programming is needed for the first picture: UEFI leaves the
  panel scanning out the GOP buffer, and the legacy backend copies the
  compositor's frames there.

## Pieces

1. **Prelinked `IOMobileGraphicsFamily`** — `board.toml [kernel.prelink] roots`
   now includes `com.apple.iokit.IOMobileGraphicsFamily` and
   `com.apple.iokit.IOSurface`. The prelink plan (`kernel.json`,
   `kernel.map.json`) is uploaded with the kernel artifact and gives the
   family's `__TEXT_EXEC`/`__DATA_CONST` extents.
2. **Display ABI audit** — `scripts/audit-mobile-graphics-legacy.py` from the
   UniXNU tree identifies the original 358-slot
   `IOMobileFramebufferLegacy` vtable, the 9 abstract leaves, the constructor
   chain and the surface getters. The published sheng/pipa scripts hardcode the
   sheng candidate's addresses; for gtaxllte they must be rebased by
   `our_family_text_va - reference_family_text_va` and the family segment bytes
   compared (same iPad 17.7.11 21H461 kernelcache).
3. **Backend** — `drivers/GopFramebuffer/NativeLegacyDisplay.cpp`,
   `NativeGOPPresenter.cpp` and the pipa `LegacyDisplayProbe.cpp` (GOP/flat
   framebuffer variant, no DPU/GPU). Strip pipa's key-store, HID, touch,
   battery, MDSS/flip probes and the GPU firmware objects.
4. **Build** — copy pipa's `scripts/build-gop-legacy-display.py`, adapt the
   reference candidate to ours, and produce
   `build/native-legacy-display/libNativeLegacyDisplay.late.a` plus the
   board probe object. Add `build-gop-legacy-display.py` to `[kernel] archives`
   and `CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y`; the XNU build script already has
   the `NATIVE_LEGACY_DISPLAY` force-load block and hash check.
5. **Boot** — CPU copy path (`fb-gpu` off), `vsync=60` initially; the panel
   rate can be measured later. The GOP mapping is the only scanout.

## Order

1. Build the prelink with the display closure and verify the family appears in
   `kernel.json` extents.
2. Rebase the audit against our candidate and produce the ABI header/bindings.
3. Compile the backend + probe, link, stage, boot.
4. Confirm the panel leaves the UEFI menu and the render server presents
   frames. Then add touch and the T830 GPU.

## References

- `UniXNU/GopFramebuffer` `NativeLegacyDisplay.cpp`, `NativeGOPPresenter.cpp`,
  `OriginalMobileFramebufferABI.h`.
- `UniXNU/UniXNU-sheng` `scripts/build-gop-legacy-display.py`,
  `package/LegacyDisplayProbe.cpp`, `package/DisplayTimingProbe.cpp`.
- `pipa/scripts/build-gop-legacy-display.py`,
  `pipa/package/LegacyDisplayProbe.cpp` (GOP/flat variant).
- UniXNU `scripts/audit-mobile-graphics-legacy.py`.

## Verified candidate (2026-11-17)

The prelink with the display closure succeeded. From `kernel.json`:

- `IOMobileGraphicsFamily __TEXT_EXEC` VA **0xfffffff00576c000** (size 225140)
- family text SHA-256 **2e044ee68c1650397e1435234ca6459206262e4c7ed709cb7131c6f664f9bd9b**
- `slide = -0x1000000`

That base is exactly the reference the sheng/pipa audit hardcodes
(`legacy_vptr 0xfffffff0057865d8` = base + 0x1a5d8), so the published
legacy-display ABI addresses apply to this kernel without rebasing. The next
step is to adapt `UniXNU-sheng`'s `build-gop-legacy-display.py` to this
candidate (use the plan JSON as the reference, skip the QEMU gdb observation
via the recorded runtime hash, keep only the GOP/display probe) and compile
`NativeLegacyDisplay` + `NativeGOPPresenter` into the late archive.

## Build integration (2026-11-17)

`scripts/build-gop-legacy-display.py` is in `[kernel] archives` and
`CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y`. The ABI audit needs a prelinked candidate
of the exact profile, while the kernel step builds the archive before it
prelinks. The workflow therefore runs `unixnu build kernel` once first: the
recipe writes a bootstrap archive whose `native_legacy_display_probe` and
`native_legacy_display_late_probe` are empty, and the prelink pass produces
`kernel/kernel.macho` + `kernel.json`. The normal package run then rebuilds the
archive against that reference, relinks the native kernel and prelinks again.

The probe marks its stages on the framebuffer bottom row:

| marker | meaning |
| --- | --- |
| 295 | legacy probe entered |
| 296 | meta-class constructors and `postModLoad` completed |
| 297 | `PE_current_console` returned a usable 32-bit GOP |
| 298 | display object `init()` succeeded and was retained for phase 4 |
| 299 | late probe (end of `bsd_autoconf`) entered |
| 300 | `IOCoreSurfaceRoot` appeared |
| 301 | the synthetic `gop-display` nub started |
| 302 | `NativeLegacyDisplay::start` succeeded |
| 303 | late-probe failure path |

With phase 4 and `offline-legacy-transfer=1`, the first accepted swap is copied
into the GOP; the UEFI menu should be replaced by the iPadOS frame.

## Device result (2026-11-18)

The prelinked display kernel runs on the SM-T585. The first version registered
the native subclass before `IOKitBSDInit` and stalled `bsd_autoconf` after the
display object was retained, so the probe now does all class registration,
allocation and start from the late probe (after `IOKitBSDInit`).

The full-width stage bars show every step on the panel:

- prepare / preModLoad / postModLoad / console / allocate / init
- late probe entered
- `IOCoreSurfaceRoot` found
- GOP nub started
- `startHardware` / `start` reached
- `registerService` published the display (silver bar)
- no failure bar
- no first-swap bar yet

The display service is therefore live and registered. The panel still shows the
loader menu because no compositor has submitted a frame: `backboardd` and
SpringBoard need the static trust cache and the Data/SystemOS startup, which are
the next workstream. The T830 GPU remains a separate port.
