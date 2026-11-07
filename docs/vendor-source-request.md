# TCH REL3.3 source and independently licensed binary terms request

This is an owner-ready draft, **not a sent message**. Its immediate consumer is the
release owner assembling the complete-image component/source inventory. The owner
selects the existing iWave BSP/support contact associated with the delivery; no
recipient address or private purchase/account details are published here.

Before sending, supply privately: the delivery/order reference, actual device model
and PCB/modem revision, the intended image/release identifier, and the intended
distribution channel. Attach the public source-gap/module identity table and the
vendor's own delivery manifest. Do not attach private installation profiles, SIM
settings, account credentials or signing/device keys.

## Draft

**To:** Existing iWave technical support/BSP delivery contact, with the appropriate
library/firmware licensing contact copied by the owner.

**Subject:** TCH G62H i.MX91 REL3.3: actual build sources and component redistribution terms

Hello,

We are preparing a Linux platform release for the Telematics Connect Hub G62H,
i.MX91, based on the supplied REL3.3 BSP. We maintain GPL-covered kernel extensions
and an explicitly scoped public source release. We want to pair any distributed
binaries with their actual corresponding source and satisfy the separate terms
of independently licensed libraries/firmware.

Our recorded stock rootfs SHA-256 is
`4812a431aa677c6cdafe47a1e429902ecd15b8200ab47dd05bf4d23334c16d98`,
with factory kernel `6.12.34-lts-next-gb1aec8c1dd0d`. The delivery manifest revision
is `7d532c15bf850b29621e9feca9df67704dc5ba88`, file
`iwg50m_osms_6.12.34_2.2.0_1.0.xml`.

### A. Actual preferred sources and build mapping for GPL components

Please identify/provide the preferred editable sources, effective recipes/appends,
patches, configuration/interface definitions and scripts controlling build/install
for the following delivered modules:

| Module | Recorded binary SHA-256 | Public source candidate already identified |
|---|---|---|
| `cryptodev.ko` | `0041b731cfaff262bb20dded6d839d04f796c45e259e0d7d0c4d8f7361101c09` | cryptodev-linux `135cbff90af2ba97d88f1472be595ce78721972c`, package `cryptodev-module` 1.14-r0 |
| `igb_uio.ko` | `2e51d07348d7aa5b79ca519b6f83137fe782990bd5ccf2ee4926822408457dfa` | dpdk-extras `e9e32e999c3c0d6b3edead9ad51746e900572f75` |
| `kpage_ncache.ko` | `a34a0b8ecc864077e1a10fd40ae5dc1fc462ceb141f6f5a62979911a8bab6fda` | dpdk-extras, same revision |
| `mlan.ko` | `134d3bc084bb5cab675fd7e1abed8536ac421e164f2952cbad28ceadd33a66fc` | mwifiex `1e1f69bf38369807443e8f567cac63e26fe80881`, branch `lf-6.12.34_2.1.0`, version 540.p7 |
| `moal.ko` | `053ac7490b0d5d0f52479256be200c94568b5eb08886ad54d24b33c3731bd7a0` | mwifiex, same revision/version |

We have not established whether additional vendor patches were used. If the public
candidates with the identified effective recipes are the complete sources, please
confirm that mapping and identify any required generated/staged headers or other
source/build inputs. If extra changes were used, please supply them in preferred
editable form. A build manifest or source-package mapping is useful; we are not
requesting a bit-identical rebuild as a GPL condition.

Please also provide or identify the complete corresponding sources/configuration
and build/install scripts for the actual U-Boot/SPL and GPL-covered recovery
kernel/initramfs components distributed in the flashing bundle. Our retained U-Boot
recipe records revision `b68af07c82eb869537862fc59478f73b0187762e` with twelve patches;
the ordinary kernel recipe records `02cf250c2e6bd375c11dae324ea145a39015d41b` with
fifteen patches. Please distinguish the normal installed kernel from recovery
`Firmware/Image_uuu`, and identify the components inside both boot containers.

For the complete normal/recovery rootfs, please supply the release's package and
license/source manifest, applicable source archives/patches and recipe/configuration
mapping for GPL/LGPL components. A Yocto license manifest or SPDX inventory, together
with the matching source delivery, would help map each installed binary to its
license obligations. Please identify any existing source offer covering that delivery
and how recipients obtain its source.

This section concerns source delivery and factual build mapping under licenses
already attached to the GPL-covered code. We are not asking for additional permission
to exercise GPL redistribution rights, or for credentials to a private source account
as the public recipient's sole source-access route.

### B. Existing terms for separate library/header and firmware components

We received two distinct `libTelematics.so` variants: the stock rootfs member
`usr/lib/libTelematics.so` (397,504 bytes) and the separate
`TelematicsLibrary/libTelematics.so` (1,187,720 bytes, SHA-256
`57c950f2e7163cd3ed48e816f3a2b84d098603f41568bcd65fa7ae8d2d01ed61`).

Please identify the existing license/EULA/contract terms applicable to each library
variant and the ten supplied API headers. In particular, do the existing terms cover
redistribution in a modified TCH device image, availability of that image as a public
download, and distribution of the necessary headers/notices? Please identify the
appropriate variant and any required attribution, packaging or scope conditions.
If an intended use is not already granted, please identify the specific additional
terms available for that component. We do not infer that the absence of a license
notice in the header directory means distribution is prohibited.

Please provide the per-file/package license/EULA and notice mapping for the radio,
DDR/boot or other independently licensed firmware actually included in this normal
and recovery delivery. Please separate source components from binary firmware and
identify any applicable existing redistribution grants or conditions. A GPL driver
license alone does not identify the license of firmware loaded by that driver.

A component-by-component answer is sufficient. No new blanket consent for the
Linux kernel, our GPL changes or other already licensed open-source components is
requested. Please retain the original authorship and license notices in any supplied
source package.

Thank you.

## How the owner uses the response

Record which delivery/file hashes the answer covers. Close an actual source-input
gap when preferred sources and effective build mapping are established; an answer
that no extra patches exist can resolve an uncertainty without inventing a missing
patch task. Evaluate independent library/firmware terms against the intended image
channel and retain the actual grant/notices. A response about one component does not
clear every rootfs package or qualify hardware. No message has been sent by this
repository workflow or by creation of this draft.

The stock package list in this project identifies 2,720 installed packages from
575 recipe names. Its dpkg metadata has no License or Source fields, and the
stock image contains no `license.manifest` or `license_image.manifest`. Please
provide the matching Yocto license/source export rather than only a generic
upstream package list. The inventory includes NXP Wi-Fi firmware packages and
`quectel-firmware`; please identify the applicable redistribution notices/terms
for the actual included variants. This request does not imply those terms prohibit
distribution or seek an additional permission for code already licensed under GPL.
