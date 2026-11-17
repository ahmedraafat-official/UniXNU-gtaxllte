# GopFramebuffer pin (e47c1b8)

This is the exact UniXNU/GopFramebuffer revision
`e47c1b8de169cd2bd0b885c7d85e433010e3cdf2`, vendored because the UniXNU
checkout used by the packaging workflow pins its `drivers/GopFramebuffer`
submodule at `b0d812d`. That later source calls `native_legacy_ioav_*` and
`native_legacy_hotplug_notify`, which this board's legacy display archive does
not provide. The legacy ABI audit and the absolute bindings were qualified
against e47c1b8.

The driver is unchanged; replace this directory with a new upstream revision
only after rerunning `scripts/build-gop-legacy-display.py` and the ABI checks.
