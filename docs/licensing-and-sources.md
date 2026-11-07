# Source rights and firmware distribution

Linux is provided under GPL version 2 only, with its syscall exception and the
applicable per-file notices. The project retains the existing source/UAPI SPDX
identifiers. Original project tooling is supplied under GPL-2.0-only unless an
included file states another compatible license. The LICENSES directory contains
the relevant texts. Copyright notices identify authorship; they are not removable
branding.

A private vendor Git server is a source-acquisition detail, not evidence that all
adjacent files share the kernel's license. This project does not relicense the
vendor rootfs, proprietary library, firmware, SDK or toolchain by storing a local
copy under .local. Git excludes those local inputs. The public source export is
an explicit set of components, tools, profiles and tests.

## Existing source gap

The qualified kernel set preserves five external modules: cryptodev, igb_uio,
kpage_ncache, mlan and moal. Public candidate sources and pinned recipe revisions
have been identified from the supplied BSP manifest and the stock root filesystem
package metadata. Correspondence to the five delivered factory module binaries is
not yet verified. A loader-compatible module with a GPL string is not complete
corresponding source.

### Evidence linking packages to candidate sources

The stock rootfs SHA-256 is
`4812a431aa677c6cdafe47a1e429902ecd15b8200ab47dd05bf4d23334c16d98`.
A bounded read of its `var/lib/dpkg/status` maps each binary package to its `OE`
recipe and package version. All five use `PackageArch: imx91-iwg50m-osm` and depend
on kernel `6.12.34-lts-next-gb1aec8c1dd0d`.

| Modules | Package `OE` recipe | Package version | Candidate source revision |
|---|---|---|---|
| cryptodev | cryptodev-module | 1.14-r0 | `135cbff90af2ba97d88f1472be595ce78721972c` |
| igb_uio, kpage_ncache | kernel-module-dpdk-extras | git-r0 | `e9e32e999c3c0d6b3edead9ad51746e900572f75` |
| mlan, moal | kernel-module-nxp-wlan | git-r0 | `1e1f69bf38369807443e8f567cac63e26fe80881` |

The supplied `iwave-imx9-manifest` revision
`7d532c15bf850b29621e9feca9df67704dc5ba88`, file
`iwg50m_osms_6.12.34_2.2.0_1.0.xml`, pins Poky
`b642d6a4a93a23d5b99886844ff7b224aa7e13bf`, meta-freescale
`167ff4ddb79299cf5477e94620a9e598659c407b`, meta-imx tag
`rel_imx_6.12.34_2.1.0`, and meta-iwave-imx9
`de7f19bdecbb257f20551aec3a0c6d16575253fe`.

The resulting public recipe chain is:

- **cryptodev:** the [pinned Poky module recipe](https://github.com/yoctoproject/poky/blob/b642d6a4a93a23d5b99886844ff7b224aa7e13bf/meta/recipes-kernel/cryptodev/cryptodev-module_1.14.bb)
  requires [cryptodev.inc](https://github.com/yoctoproject/poky/blob/b642d6a4a93a23d5b99886844ff7b224aa7e13bf/meta/recipes-kernel/cryptodev/cryptodev.inc),
  which declares GPL-2.0-only and pins [cryptodev-linux source](https://github.com/cryptodev-linux/cryptodev-linux/tree/135cbff90af2ba97d88f1472be595ce78721972c).
  The NXP [RSA bbappend](https://github.com/nxp-imx/meta-imx/blob/rel_imx_6.12.34_2.1.0/meta-imx-bsp/recipes-kernel/cryptodev/cryptodev-linux_1.14.bbappend)
  targets `cryptodev-linux`, not `cryptodev-module`. Do not assume it patches
  cryptodev.ko; verify the actual module and staged-header inputs.
- **igb_uio and kpage_ncache:** the [pinned NXP dpdk-extras recipe](https://github.com/nxp-imx/meta-imx/blob/rel_imx_6.12.34_2.1.0/meta-imx-bsp/recipes-kernel/kernel-modules/kernel-module-dpdk-extras_git.bb)
  declares GPL-2.0-only and pins [nxp-qoriq/dpdk-extras](https://github.com/nxp-qoriq/dpdk-extras/tree/e9e32e999c3c0d6b3edead9ad51746e900572f75/linux).
  kpage_ncache.c has SPDX GPL-2.0 although its module metadata says Dual BSD/GPL.
  Preserve the file license and notices; do not infer a BSD license for all of
  the source from that module string.
- **mlan and moal:** the [pinned meta-freescale recipe](https://github.com/Freescale/meta-freescale/blob/167ff4ddb79299cf5477e94620a9e598659c407b/recipes-kernel/kernel-modules/kernel-module-nxp-wlan_git.bb)
  declares GPL-2.0-only. The [NXP bbappend](https://github.com/nxp-imx/meta-imx/blob/rel_imx_6.12.34_2.1.0/meta-imx-bsp/recipes-kernel/kernel-modules/kernel-module-nxp-wlan_git.bbappend)
  replaces its source URI, branch and revision with [mwifiex source](https://github.com/nxp-imx/mwifiex/tree/1e1f69bf38369807443e8f567cac63e26fe80881)
  on `lf-6.12.34_2.1.0`. The candidate Makefile declares `540.p7`, matching the
  binaries. The recorded binary srcversions are `0C4C1DDD98BA0614E8EFEAD` for
  mlan and `3F3831B352F67ECFA142402` for moal. Matching the version string
  does not establish matching source.

The remaining check is to confirm the actual vendor build inputs, local patches,
generated/staged inputs and configuration against the five recorded factory module
hashes. The profile's source-gaps.json retains those hashes and the candidate
mapping. No reproduction build or source-to-binary acceptance was performed in
this investigation. Public candidate sources reduce the acquisition gap; they do
not close the corresponding-source gate.

### Vendor library rights

The inspected material contains two different libTelematics.so variants:

| Location in supplied material | Bytes |
|---|---:|
| Stock rootfs `usr/lib/libTelematics.so` | 397,504 |
| Separate `TelematicsLibrary/libTelematics.so` | 1,187,720 |

The separate library SHA-256 is
`57c950f2e7163cd3ed48e816f3a2b84d098603f41568bcd65fa7ae8d2d01ed61`.
Do not treat the two sizes as one interchangeable source or binary artifact.

The supplied API headers and bounded named rootfs package/license metadata did
not establish public redistribution terms for either library variant or the
related headers. No matching Telematics package/license was found in the inspected
metadata. This is an inspection result, not a determination of the full vendor
contract. Redistribution rights remain unconfirmed.

This is not a reason to remove the factory monitor from a local image and silently
lose its protection path. The project currently keeps images containing that library local while its
redistribution terms and the recorded source mappings remain unresolved.
The separately licensed kernel source is already published. Neither library is part of the public
Git source export or source-only release.

## Release contents

A development source release can contain the licensed patches, source, portable
tooling and tests. A corresponding-source release for a binary kernel/module set
must identify the exact source/config/Device Tree and necessary build/install
scripts, together with the relevant included-module sources and original notices.
It must be obtainable by the binary recipient without a private application
checkout or vendor login.

The original kernel Git HEAD alone is insufficient: the actual build includes
vendor patches and added source files not present in that base commit. Preserve
those transformations and exact preferred editable source. Do not put .git,
compiler outputs, device configuration or unrelated proprietary rootfs payloads
into the package merely because they happened to be next to the source.

A source package excluding the unresolved external module sources must say so.
It must not be described as complete corresponding source for all 814 delivered
modules or the whole device image. Likewise, a technical image PASS does not clear
proprietary binary redistribution or prove compliance for other rootfs packages.

## Application separation

Normal userspace use of the kernel's syscall/UAPI boundary does not by itself
require publishing an entire unrelated application. Application signing keys,
service authority and installation secrets are not public kernel source. An
application-specific image still needs its own component inventory and source
compliance process for the components it actually distributes.

## References

- [Linux kernel licensing rules](https://docs.kernel.org/process/license-rules.html)
- [GPLv2 text](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)
- [GNU licensing FAQ](https://www.gnu.org/licenses/gpl-faq.html)
- [GitHub large-file/release guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
