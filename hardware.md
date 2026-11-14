# Hardware inventory

Every value names a public source until Phase 0 supplies the stock DTB. The
stock DTB wins on any disagreement.

## Memory

| Range | Size | Use | Source |
| --- | --- | --- | --- |
| `0x40000000`–`0x7e400000` | 0x3e400000 | bank 0 | mainline gtaxlwifi DTS |
| `0x7e400000`–`0x80000000` | 0x01c00000 | secure-world hole (do not map) | mainline gtaxlwifi DTS |
| `0x80000000`–`0xc0000000` | 0x40000000 | bank 1 | mainline gtaxlwifi DTS |

Firmware carveouts from `gtaxlltePkg/Library/MemoryMapLib/MemoryMapLib.c`:

| Range | Size | Use |
| --- | --- | --- |
| `0x43e00000` | 0x200000 | UEFI FD |
| `0x44000000` | 0x40000 | UEFI stack |
| `0x44040000`–`0x49440000` | 0x05400000 | DXE heap |
| `0x52a00000` | 0x200000 | TIMA |
| `0x67000000` | 0x1194000 | display / framebuffer |
| `0x80000000`–`0x89400000` | 0x09400000 | ION heaps |
| `0x90000000` | 0x14000 | ECT (DVFS tables) |
| `0xae600000` | 0x400000 | MFC |
| `0xaffff000` | 0x1000 | DRAM init |
| `0xb0000000` | 0x7800000 | modem IF |
| `0xb7c00000` | 0x400000 | GNSS IF |

The largest free run in bank 1 is `0x90014000`–`0xae600000` (about 470 MiB).
M2 requests 448 MiB (`0x1c000000`). Releasing the ION and MFC carveouts in the
UEFI map is an M4 optimization; the modem and GNSS regions stay reserved.

## Core

| Item | Address / value | Source |
| --- | --- | --- |
| SoC | Exynos 7870 (S5E7870), 8x Cortex-A53 | mainline DTSI |
| CPU MPIDR | 0x000–0x007 | mainline DTSI |
| GIC-400 | GICD `0x104e1000`, GICC `0x104e2000`, GICH `0x104e4000`, GICV `0x104e6000` | mainline DTSI |
| Timer | 26 MHz; PPIs 13/14/11/10; CNTFRQ not set by S-Boot | mainline DTSI, UEFI errata |
| PSCI | 0.2 over SMC | mainline DTSI |

## Peripherals (for later milestones)

| Item | Address / value | Source |
| --- | --- | --- |
| eMMC (DWMMC) | `0x13540000` | mainline DTSI |
| Debug UART | `0x13820000` (ttySAC2) | mainline gtaxlwifi DTS |
| DECON | `0x14830000` | mainline DTSI |
| DSIM | `0x14890000` | mainline DTSI |
| Panel | HX8279D, 1200x1920, 32 bpp framebuffer | mainline gtaxlwifi DTS |
| Touch | STMicro FingerTipS, I2C1 `0x49`, IRQ GPA0-4 | mainline gtaxlwifi DTS |
| PMIC | S2MPU05, I2C `0x66` | mainline gtaxlwifi DTS |
| Charger / fuel gauge / MUIC | SM5703, I2C4 `0x49` | mainline gtaxlwifi DTS |
| GPU | Mali-T830 MP1, `0x11400000` | mainline DTSI |
| Wi-Fi | QCA9377 on SDIO (mmc1) | mainline gtaxlwifi DTS |
| Bluetooth | QCA9377 on UART1 | mainline gtaxlwifi DTS |

## Open questions

- Does this exact SM-T585 unit meet the Mu-Silicium port's **BIT-7** revision
  requirement? The Download-mode screen and the stock DTB should identify it.
- The stock partition names, BOOT size and `userdata` geometry (the Wi-Fi DTS
  is not the LTE layout).
- Whether the LTE model's panel and touch controller are identical to the
  Wi-Fi model's.
- The live UEFI memory map: the `MemoryMapLib` above is the build-time table;
  DarwinBoot reports the actual free runs at boot.
- The UART route for XNU logs. The UEFI has ttySAC2; XNU has no Samsung UART
  driver yet, so M1/M2 use the panel and the UniXNU menu log.
