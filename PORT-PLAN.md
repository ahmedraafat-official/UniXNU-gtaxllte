# Port plan

The order is deliberate: prove the boot chain, then the kernel entry, then
storage, then the UI. Every milestone has one expected observation and one
artifact to keep.

## M0 — inventory and backups (Phase 0)

**Do:** unlock, TWRP, dump `boot/recovery/dtb/efs/modem/...`, live
`/proc/device-tree`, `getprop`, `dmesg`, BOOT size.

**Expected:** a `gtaxllte-backup/` folder with a readable stock DTB.

**Blocks:** every later milestone; the DTB is the authority for the memory map
and the `BIT-7` question.

## M1 — UEFI and the UniXNU menu

**Build:** Mu-Silicium `gtaxlltePkg` with
`patches/uefi/0001-gtaxllte-unixnu-payload.patch`, UniXNU package + DarwinBoot
in the FV, Android header v1, FD base `0x43e00000`.

```sh
export UNIXNU_UEFI=/path/to/Mu-Silicium
cd ../unixnu
./unixnu fetch -D ../UniXNU-gtaxllte
./unixnu build package -D ../UniXNU-gtaxllte
./unixnu build firmware -D ../UniXNU-gtaxllte
./unixnu bootimg -D ../UniXNU-gtaxllte --ramdisk none
```

Flash `out/gtaxllte/.../boot/boot.img` to `BOOT` from TWRP, or use Odin/Heimdall
in Download mode. There is no `fastboot boot` on this Samsung.

**Expected:** the UniXNU menu on the tablet's panel; the UEFI sees the eMMC and
the side buttons. The menu without a payload is a success for M1.

**Risks:** the `BIT-7` revision requirement; the UEFI build needs Mu_Basecore
and the submodules; BOOT size must fit BootShim + 2 MiB FD.

## M2 — XNU entry

**Build:** `gtaxllte_defconfig` + `exynos7870` SoC layer, prelinked against the
iPadOS 17.7.11 `iPad7,1` kernelcache. One CPU, GIC-400, 26 MHz timer, owned
window `0x1c000000` in the second bank. No storage/display/input driver.

**Expected:** XNU starts, prints its early log/panic screen, and stops at
`Failed to mount root device` — exactly like the first pipa image. This proves
the CPU, GIC, timer and DarwinBoot handoff.

**Risks:** the free run may be smaller than the requested window; the GIC-400
banked interface and the 26 MHz timebase may need the same fixes as `hero2lte`;
the secure hole must stay out of the owned window.

## M3 — storage and launchd

**Build:** an Exynos DWMMC/eMMC block driver (new component; the Linux
`dw_mmc-exynos` register layout is the reference), read-only GPT first, then the
OS/SystemOS windows in `userdata` and a Data seed.

**Expected:** the OS volume mounts, `launchd` starts, `ps` shows the first
daemons.

**Risks:** the SM-T585 partition geometry differs from the Wi-Fi model; the
UEFI's MshcDxe may leave the controller in a state the XNU driver must adopt;
writes must be fenced to `userdata`.

## M4 — display, input and SpringBoard

**Build:** `GopFramebuffer`/legacy display on the 1200x1920 buffer; the
AppleKeyStore stand-in; STMicro FTS touch driver; a trimmed daemon set for
2 GiB. Release the unused ION heaps in the UEFI memory map to grow the owned
window towards 768 MiB.

**Expected:** the iPadOS welcome screen, then SpringBoard. Settings opening is
a bonus, not the milestone.

**Risks:** 2 GiB total RAM and the 28 MiB secure hole; the 16:10 panel vs the
iPad profile's 4:3 assets; touch not working even in the UEFI port; SpringBoard
may jetsam daemons before it finishes.

## M5 — GPU, Wi-Fi, audio, power

**Build:** Mali-T830 through Mesa Panfrost (OpenGL ES for CoreAnimation
first); QCA9377 SDIO Wi-Fi; S2MPU05/SM5703 battery and charger; ABOX/audio.

**Expected:** GPU-composited UI, Wi-Fi joined from Settings, battery percentage.

## Non-goals

- App Store, Apple ID, iCloud, activation servers.
- The LTE modem (the CP memory stays reserved).
- A daily-driver Android replacement. The tablet boots UniXNU from `BOOT`;
  Android remains intact unless `userdata` is flashed.
