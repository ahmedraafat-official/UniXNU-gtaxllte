# Bring-up Data seed and original trust bundle

These two files are the inputs of the first userland bring-up on the SM-T585.
They are not an Apple-signed Data volume and they are not an owner-policy
envelope.

## `owner-data-8g.dbr`

The packed 8 GiB APFS Data seed built on 2026-11-18 from the mounted
iPadOS 17.7.11 (21H461) OS volume `DawnUpdate21H461.J120J121J207J208OS`:

```sh
python3 unixnu/scripts/prepare-owner-data-fixture.py --output build/owner-data-fixture
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'drivers/DarwinBoot/Test')
import packed_ramdisk
packed_ramdisk.pack(Path('build/owner-data-fixture/owner-data-32.cdr'),
                    Path('build/owner-data-fixture/owner-data-32.dbr'))
PY
python3 unixnu/scripts/seed-data-grow.py --gib 8 \
  build/owner-data-fixture/owner-data-32.dbr \
  build/owner-data-fixture/owner-data-8g.dbr
```

- APFS volume UUID: `76BACD4A-80F1-48F0-B5B6-5A3DE1F123A5`
- Logical size: 8 GiB, packed (only nonzero pages): 1,040,512 bytes
- Seed SHA-256: `43afd2c58ab52590d7feb9f63010db663e70adad85699f06c76d2a9ac3e711a4`
- Content: the original `/private/var` tree of the OS image excluding
  `staged_system_apps`, plus empty `keybags`, `mobile/tmp`, `tmp`, `run`
  directories.

The defconfig pins the same UUID in `CONFIG_XNU_OWNER_DATA_VOLUME_UUID`.

## `original-static-system.bundle`

The two unchanged original trust-cache modules from the same IPSW, combined in
the bundle format DarwinBoot's `ValidTrustCaches` accepts:

- static module: payload of `044-76040-038.dmg.trustcache`, SHA-256
  `da88460e6e513438d5307e5c526f76497a74754476a24da29f239f0c90120fc2`
- System module: payload of `044-76034-038.dmg.trustcache`, SHA-256
  `d121ba89167e03aafd00fa687c30cd8ebc4ccafd10c1408adb2ba3de12403651`
- bundle SHA-256: `7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`

The bundle is passed to the payload as `--trust-cache`; the previous failed
attempt used the raw IM4P `trst` file, which `ValidTrustCaches` rejects as
`Compromised Data`.
