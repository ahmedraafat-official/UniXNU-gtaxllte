# Phase 0 — hardware prep and dumps

Do these in order. Steps 1 and 2 **wipe the tablet**; back up anything you care
about first. The goal is a set of files and outputs that replaces every public
address in this repository with a measured one.

You need:

- the SM-T585, charged above 50%
- a USB data cable and a computer (Windows with Odin is easiest; macOS works
  with Heimdall)
- a microSD card if you have one (otherwise pull to the computer with `adb`)
- the tablet's current Android booted once, with USB debugging enabled

## 1. Record the device before touching it

On Android, enable **Settings → About tablet → tap Build number 7 times**, then
**Developer options → USB debugging**. Connect USB and accept the prompt, then
on the computer:

```sh
adb devices
adb shell getprop > gtaxllte-getprop-android.txt
adb shell cat /proc/device-tree/model
adb shell cat /proc/device-tree/compatible
adb shell cat /proc/meminfo
adb shell cat /proc/cmdline
adb shell dmesg > gtaxllte-dmesg-android.txt 2>&1 || true
adb shell "ls -l /dev/block/by-name" > gtaxllte-byname-android.txt 2>&1 || true
```

Also note the model number on the box/sticker and the Android build number.

## 2. Unlock the bootloader

1. **Developer options → OEM unlock** → on.
2. Power off completely.
3. Hold **Volume Down + Home + Power** until the warning screen; press
   **Volume Up** to enter Download mode.
4. On the Download-mode screen, follow the on-screen unlock prompt (on this
   generation it is usually a long press of **Volume Up**). The tablet wipes
   and reboots. Knox is tripped permanently.
5. Boot Android once, re-enable Developer options → USB debugging, and confirm
   **OEM unlock** is greyed out/on. Power off.

If the OEM unlock toggle is missing, stop and report it; do not flash anything.

## 3. Install TWRP

Official recovery page:
<https://twrp.me/samsung/samsunggalaxytaba101lte2016.html>

Download the **SM-T585 (gtaxllte)** build. Use the `.tar` with Odin (Windows)
or the `.img` with Heimdall (macOS/Linux).

**Odin (Windows):**

1. Boot Download mode again (Vol Down + Home + Power, then Vol Up).
2. Odin: put the TWRP `.tar` in **AP**, uncheck **Auto Reboot**, flash.
3. When it says PASS, hold **Volume Down + Home + Power** to leave Download
   mode, then immediately hold **Volume Up + Home + Power** to boot TWRP before
   Android can restore the stock recovery.

**Heimdall (macOS/Linux):**

```sh
heimdall print-pit                       # confirms the tablet is visible
heimdall flash --RECOVERY twrp-gtaxllte.img --no-reboot
```

Then the same key sequence: Vol Up + Home + Power into TWRP.

TWRP may ask to allow system modifications; choose **Keep read-only** for now.

## 4. Back up the partitions that can brick it

In TWRP, connect USB. Check `adb devices`; accept the authorization prompt on
the tablet. Then open **Advanced → Terminal** (or use `adb shell`) and run:

```sh
mkdir -p /sdcard/gtaxllte-backup
for p in boot recovery dtb dtbo param efs modem modemst1 modemst2 fsg fsc \
         persist sec steady vbmeta misc; do
  if [ -e /dev/block/by-name/$p ]; then
    echo "dumping $p"
    dd if=/dev/block/by-name/$p of=/sdcard/gtaxllte-backup/$p.img bs=4M
  fi
done
```

Then the live hardware description and logs:

```sh
cp -r /proc/device-tree /sdcard/gtaxllte-backup/device-tree
cat /proc/meminfo > /sdcard/gtaxllte-backup/meminfo.txt
cat /proc/cmdline > /sdcard/gtaxllte-backup/cmdline.txt
cat /proc/partitions > /sdcard/gtaxllte-backup/partitions.txt
getprop > /sdcard/gtaxllte-backup/getprop-twrp.txt
dmesg > /sdcard/gtaxllte-backup/dmesg-twrp.txt
ls -l /dev/block/by-name > /sdcard/gtaxllte-backup/byname-twrp.txt
```

Record the BOOT partition size (the payload limit):

```sh
blockdev --getsize64 /dev/block/by-name/boot 2>/dev/null \
  || cat /sys/class/block/$(basename $(readlink -f /dev/block/by-name/boot))/size
```

On a microSD card, the paths above are `/external_sd/...`. Pull everything to
the computer:

```sh
adb pull /sdcard/gtaxllte-backup ./gtaxllte-backup
```

Verify the important images are non-empty, then copy them somewhere off the
tablet. Without these, restoring a bad BOOT flash is much harder.

## 5. Send back these files/outputs

Put them in the project folder (or send them here):

- `gtaxllte-backup/device-tree/` — the live stock DTB (the most important)
- `gtaxllte-backup/byname-twrp.txt` and `partitions.txt`
- `gtaxllte-backup/meminfo.txt` and `cmdline.txt`
- the BOOT partition size output
- `gtaxllte-getprop-android.txt` and `gtaxllte-dmesg-android.txt`
- `gtaxllte-backup/dmesg-twrp.txt`
- a photo of the **Download mode** screen (it shows the model, CARRIER, and
  sometimes the hardware revision)

The DTB settles the memory map, panel timing, touch controller, PMIC and the
`BIT-7` revision question. The Mu-Silicium port notes that a **BIT-7** version
is required; if the UEFI image refuses to boot later, this is the first thing
we check.

## 6. What happens next

With the DTB in hand:

1. `hardware.md` and `dts/gtaxllte.dts` get the measured ranges, replacing the
   public gtaxlwifi values.
2. Milestone **M1** builds the Mu-Silicium `gtaxllte` UEFI with UniXNU and
   DarwinBoot added, packs it into an Android boot image, and boots it from
   `BOOT` (no XNU yet). Expected result: the UniXNU menu on the tablet.
3. Then M2 builds the XNU kernel for Exynos 7870 and expects a panic at the
   root mount, with the log on the framebuffer.

Do not flash a UniXNU boot image before M1 is built and the backups are
verified.
