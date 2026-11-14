# UniXNU-gtaxllte

Port of the UniXNU iPadOS environment to the **Samsung Galaxy Tab A 10.1
(2016) LTE, SM-T585**, codename `gtaxllte`, Exynos 7870 (`S5E7870`).

> **State: scaffold, nothing booted yet.** The device and SoC repositories
> resolve on paper; no firmware, XNU kernel or iPadOS userland has run on the
> tablet. Every address below names its public source and must be checked
> against the tablet's own stock DTB before the first flash.

## Why this tablet

The pieces exist:

| Piece | State |
| --- | --- |
| Boot chain | Mu-Silicium already has `Platforms/Samsung/gtaxlltePkg`; S-Boot loads it, display, eMMC and side buttons work in UEFI, Windows boots |
| SoC | Exynos 7870 is in mainline Linux: eight A53s, GIC-400, 26 MHz timer, DWMMC, DECON/DSIM, Mali-T830, S2MPU05, STMicro FTS touch |
| Board docs | [linux-exynos7870](https://github.com/alessandro-fontana/linux-exynos7870), branch `gtaxlwifi/7.3`, plus a mainline Linux 7.1 port to this tablet |
| OS profile | iPadOS 17.7.11 (`iPad7,1` userland) is the UniXNU profile closest to a 2 GB device; iPad 6 runs the same OS on 2 GB |
| Missing | The XNU side: an Exynos 7870 SoC layer, DWMMC/eMMC root, display, touch and GPU drivers |

The SM-T585's panel is 1200x1920 (16:10), not an iPad's 4:3; the first
SpringBoard milestone will use the panel's native geometry and deal with the
scale after it draws.

## Repositories

| Path | Contents |
| --- | --- |
| `board.toml` | Board, firmware, boot image, payload and ADT settings |
| `configs/gtaxllte_defconfig` | Kernel configuration of the first image |
| `dts/gtaxllte.dts` | Memory banks, secure hole, firmware carveouts, enabled CPUs |
| `patches/uefi/0001-gtaxllte-unixnu-payload.patch` | Adds UniXNU and DarwinBoot to the upstream UEFI package |
| `scripts/patch-gtaxllte-uefi.py` | Firmware preflight that applies the patch idempotently |
| `../UniXNU-exynos7870-common` | Shared Exynos 7870 SoC layer (local checkout; pin in `feeds.toml`) |

## Hardware inventory

| Item | Value | Source |
| --- | --- | --- |
| SoC | Exynos 7870, 8x Cortex-A53 (ARMv8.0) | mainline DTSI |
| RAM | 2 GiB in two 1 GiB banks; 28 MiB secure hole at `0x7e400000` | mainline gtaxlwifi DTS |
| Display | 1200x1920, framebuffer at `0x67000000`, 32 bpp | Mu-Silicium + mainline |
| UEFI | FD `0x43e00000` + 2 MiB; stack `0x44000000`; heap to `0x49440000` | `gtaxlltePkg/Library/MemoryMapLib` |
| GIC | GIC-400 at `0x104e1000` | mainline DTSI |
| Timer | 26 MHz, PPIs 13/14/11/10 | mainline DTSI |
| eMMC | DWMMC at `0x13540000` | mainline DTSI |
| Display controller | DECON `0x14830000`, DSIM `0x14890000` | mainline DTSI |
| GPU | Mali-T830 MP1 at `0x11400000` | mainline DTSI |
| Touch | STMicro FingerTipS at I2C1 `0x49` | mainline gtaxlwifi DTS |
| PMIC | S2MPU05 at I2C `0x66`, SM5703 charger/fuel gauge | mainline gtaxlwifi DTS |
| Wi-Fi | QCA9377 on SDIO (mmc1) | mainline gtaxlwifi DTS |

## Read this first

1. [PHASE0.md](PHASE0.md) — unlock, TWRP, backups and the dumps that replace
   every public value with a measured one.
2. [PORT-PLAN.md](PORT-PLAN.md) — milestones, expected results and risks.
3. [hardware.md](hardware.md) — addresses, memory map and open questions.

## Build (after Phase 0)

The local UniXNU checkout is expected at `../unixnu` (`[package] unixnu`).
`unixnu` needs Python 3.11+; this Mac currently has 3.9, so install a 3.11
first (or run the packaging on a Linux/macOS CI runner).

```sh
cd ../unixnu
./unixnu defconfig gtaxllte_defconfig -D ../UniXNU-gtaxllte
./unixnu fetch -D ../UniXNU-gtaxllte
./unixnu status --json -D ../UniXNU-gtaxllte
```

Firmware build needs a Mu-Silicium checkout with its submodules and a
Mu_Basecore checkout; the full build does not fit this Mac's free space yet.
Milestone M1 is built on an external drive or a CI runner.

## Legal

UniXNU and this repository distribute no Apple code. You supply your own IPSW.
Apple's license says iPadOS runs only on Apple hardware; running it elsewhere is
a legal grey area. Flashing this tablet trips Knox and wipes its data.
