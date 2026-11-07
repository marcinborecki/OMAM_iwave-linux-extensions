# Reuse the existing ST LSM6DSX interrupt mapping

This GPLv2 patch targets the iWave Connect Hub G62H i.MX91 REL3.3 device-tree path
for the `st,lsm6dsl` I2C IMU and gpio-vf610 two-cell interrupt domain. It is a narrow
compatibility workaround for vendor-driver handover, not a new accelerometer driver.
The actual sensor support comes from upstream `st_lsm6dsx` core and I2C modules.

## Original failure and the change

The vendor built-in `st_lsm6dsm` driver requests GPIO3 line 22 as level-high at boot,
although the device tree declares a rising edge. This changes the existing mapping.
After vendor-driver unbind the I2C core attempts to map the interrupt again, the IRQ
domain refuses the type mismatch and the new client receives no IRQ. On this kernel
that leaves triggered buffers unable to deliver the required IMU data.

When `client->irq <= 0`, the patch parses the node's first device-tree interrupt,
finds its existing interrupt domain and looks up the already allocated hardware IRQ
mapping. It neither creates a mapping nor changes its trigger type. It passes that
existing IRQ to `st_lsm6dsx_probe` and logs the fallback once. A valid IRQ follows the
unmodified upstream route. Missing nodes/domains/mappings or non-two-cell specifiers
retain the upstream no-IRQ behavior.

The hard boundary is driver ownership: the vendor driver must be detached before the
replacement binds. This patch does not perform takeover, configure FIFO/calibration,
change mount orientation or publish application data. No claim is made that this
fallback fixes other IRQ domains, board revisions or sensors. It must not be used to
hide an unrelated IRQ allocation/configuration fault.

## Exact source and build route

The patch records its original source provenance. Base is `linux-imx9-iwave` revision
`02cf250c2e6bd375c11dae324ea145a39015d41b`; the unmodified I2C source SHA-256 is
`2153bdfecdb45afd95e002a6a5ebb4d07e9357c100221c6dbba13b925029bb1c`.
The immutable patch bytes are retained so already qualified module provenance remains
verifiable. Historical report names in the patch description are provenance text,
not required files or build dependencies of this project.

`tools/kernel/qualify.py` builds only core and I2C front ends as external `obj-m`
modules against the exact candidate headers, configuration and `Module.symvers`.
It applies the patch with zero fuzz, strips debug information and verifies symbol
CRCs, export namespaces, GPL-only imports, module layout, relocations, vermagic and
dependency resolution. The aggregate manifest pins the resulting two modules and
kernel Image. The pure Image export parser is local to `tools/kernel/image_exports.py`;
no application installer or private source is imported.

The `omam_` prefix in retained patch text and manifest contracts identifies the
original extension and compatibility format; it does not install an OMAM application.
Host/static checks do not prove interrupt delivery, sample continuity, FIFO overruns,
calibration, suspend/resume or physical driver takeover. Those remain board checks.
