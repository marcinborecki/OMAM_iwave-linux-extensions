# Source rights and complete-image distribution

The Linux kernel and this project's GPL-covered changes can be published under their
existing licenses. **GPL publication does not require an additional permission from
iWave.** A vendor-hosted Git URL does not replace the license attached to the code.
Preserve authorship, license texts, compatible per-file notices and the syscall/UAPI
exception. Independent vendor libraries and device firmware have their own terms;
placing them beside Linux does not turn them into GPL-covered code.

## What GPLv2 requires, and what our hashes prove

[GPLv2 sections 1 and 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)
authorize source copying/modification with their notice and licensing conditions.
When distributing GPL-covered object code, section 3 requires a compliant source
route, such as accompanying complete corresponding machine-readable source or a
qualifying written offer. Section 3(b)'s offer lasts at least three years and is
available to any third party; section 3(c)'s inherited-offer option is limited to
its stated noncommercial circumstances. For download distribution, section 3 also
describes equivalent access to source from the designated place. Choose an actual
route for the release rather than merely linking an unrelated upstream homepage.

The required source is the preferred editable form for the version distributed,
including relevant interface definitions and scripts controlling compilation and
installation, subject to the section 3 system-component exception. Internal use
without distribution does not by itself require posting changes publicly. Public
GitHub publication is a useful delivery choice; the recipient's GPL rights do not
depend on joining a private vendor service or obtaining a separate discretionary
GPL redistribution approval. The [kernel's licensing rules](https://docs.kernel.org/process/license-rules.html)
explain its GPLv2-only license and userspace syscall boundary.

**Complete corresponding source is not a bit-identical-rebuild requirement.** The
[FSF FAQ on exact binary hashes](https://www.gnu.org/licenses/gpl-faq.en.html#MustSourceBuildToMatchExactHashOfBinary)
distinguishes the actual source used from identical output bytes: timestamps,
compiler differences and other build details can change binary hashes. Our hashes
identify which supplied binary/source/configuration a record discusses. Matching
rebuild bytes can strengthen provenance, but is not an extra condition imposed by
GPLv2. Conversely, a plausible public version or loader-compatible module is not
proof that its source includes every change used for a factory binary.

Original project tooling uses GPL-2.0-only unless a file states another compatible
license. The LICENSES directory supplies the retained texts and exceptions.

The source mapping can be resolved by credible build/source records, exact preferred
sources and applicable patches/configuration, including an affirmative statement
that no extra patches were used where appropriate. We do not claim that unconfirmed
vendor patches exist or make a successful bit-for-bit rebuild the only acceptable
way to establish correspondence.

A separately licensed library's unknown terms mean **UNKNOWN in the inspected
material**, not a finding that redistribution is prohibited. If an existing EULA
already grants the intended distribution, use that grant and meet its conditions;
request clarification or an additional grant only for the independently licensed
component whose intended use is not covered. GPL-covered parts retain their own
rights while that separate question is resolved.

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

The remaining check is to identify the actual vendor build source, whether any
additional patches were used, relevant generated/staged inputs and configuration
for the five recorded factory binaries. Their hashes identify those binaries; an
identical rebuild hash is not a GPL requirement. The profile's source-gaps.json retains those hashes and the candidate
mapping. No reproduction build or source-to-binary acceptance was performed in
this investigation. Public candidate sources reduce the acquisition gap; they do
not yet establish complete corresponding source for those five factory binaries.

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
contract. The license applicable to the intended binary/header redistribution remains UNKNOWN
from that scope. No prohibition was established by the absence of a notice.

The project retains the factory protection path in its local image. Keeping that
complete image local is the current release decision while the component evidence
is assembled; it is not a claim that GPL kernel redistribution needs vendor consent.
The separately licensed kernel source is already published. Neither library is part of the public
Git source export or source-only release.

## Evidence present, evidence not yet established

A fresh bounded inspection on 2026-11-07 verified the separately supplied
`TelematicsLibrary/libTelematics.so` identity above and inspected its ten named API
headers. Those headers contain no SPDX, copyright, license, redistribution,
confidentiality or proprietary-marker text, and no LICENSE/COPYING/EULA/NOTICE file
was present in that supplied directory. This does not establish all contract terms.
The earlier bounded library/package inspection is reused for that library statement.
The separate metadata intake below now records the installed stock package set;
it does not extract a rootfs or broaden the library license conclusion. The small
retained module receipt and acquisition manifest bind module/recipe identities.

### Actual stock normal-rootfs package intake, 2026-11-07

The allowlisted [stock package inventory](../profiles/tch-g62h-imx91-rel3.3/stock-packages.json)
now records an actual bounded metadata intake from the pinned stock rootfs. It
contains **2,720 installed packages** from **575 distinct `OE` recipe names**.
The dpkg database has 2,721 records; one is not in the installed state and is not
included in that installed-package list. `Architecture` and `OE` are present for
all 2,720 included records. **No `License` or `Source` field is present in those
records**, and no literal `license.manifest` or `license_image.manifest` member was
found among the archive headers. Thus this intake supplies **zero explicit license
entries and zero `Source` references**, with **2,720 package license fields UNKNOWN**
from the selected metadata. It supplies recipe names, not source archives or commits.
Previously verified component licenses remain separate evidence; license names
are not guessed from these package names.

The inventory comes from the 1,504,079-byte `var/lib/dpkg/status` in the
hash-identified factory rootfs. It contains only the selected package identity,
version, recipe, architecture and explicit license/source fields when present.
Descriptions, maintainer contacts and configuration are omitted. The stock package
list is not a final-image SBOM: the composed image replaces kernel content and
adds the separately documented IMU modules and .NET payload. Those changes need
their own source and notice records alongside the stock inventory.

Concrete installed recipe groups include:

| Metadata `OE` recipe | Installed binary packages | Recorded versions | License/source evidence in this intake |
|---|---:|---|---|
| `busybox` | 2 | `1.37.0-r0` | Not declared in selected metadata |
| `glibc` | 10 | `2.41+git0+6e489c17f8-r0` | Not declared in selected metadata |
| `systemd` | 15 | `1:257.6-r0` | Not declared in selected metadata |
| `linux-iwg` | 811 | `6.12.34+git0+02cf250c2e-r0` | Not declared in selected metadata |
| `openssl` | 8 | `3.4.2-r0` | Not declared in selected metadata |
| `python3` | 58 | `3.13.4-r0` | Not declared in selected metadata |
| `bash` | 2 | `5.2.37-r0` | Not declared in selected metadata |
| `coreutils` | 3 | `9.6-r0` | Not declared in selected metadata |
| `util-linux` | 126 | `2.40.4-r0` | Not declared in selected metadata |
| `dpkg` | 2 | `1.22.11-r0` | Not declared in selected metadata |
| `firmware-nxp-wifi` | 12 | `1.1-r0` | Not declared in selected metadata |
| `quectel-firmware` | 1 | `1.0-r0` | Not declared in selected metadata |
| `firmwared` | 1 | `0+git0+2e6b5db43d-r0` | Not declared in selected metadata |

These rows make the outstanding request concrete: supply the actual license/source
manifest for the **575 recorded recipes at these versions**, and connect it to the
installed packages. For components whose applicable license is GPL/LGPL, assemble
the corresponding preferred sources, effective patches/configuration and required
build/install inputs; address applicable library relinking conditions as well.
The package table alone does not determine those licenses or fulfill their terms.

Twelve packages are explicitly named under `firmware-nxp-wifi` version `1.1-r0`;
`quectel-firmware` version `1.0-r0` is also recorded. These are concrete package
candidates for per-file firmware/EULA/notice mapping. The `firmwared` package is
recorded separately; its firmware-related name does not by itself establish that
it is a firmware blob or determine its license. A package list does not establish
which firmware files a particular fitted radio uses.

This is the **stock normal rootfs**, not a complete inventory of the separately
shipped recovery initramfs, boot containers, added SDK or downstream image. Package
metadata can also omit manually installed files: absence of a Telematics entry
neither grants nor prohibits redistribution of `libTelematics.so`. The owner can
attach this safe inventory to the existing unsent vendor request to obtain the
release's version-matched package/license/source mapping without sending private
configuration. The project has not certified complete-image legal compliance.

The selected .NET SDK **does** contain `LICENSE.txt` with an MIT grant, copyright
notice and disclaimer, plus `ThirdPartyNotices.txt`. Their SHA-256 values are
`cfc21f5e8bd655ae997eec916138b707b1d290b83272c02a95c9f821b8c87310` and
`d30078edf6426ec7ef9427b3048b407fee214d1eb7834a681a3a09290387d591` respectively.
Preserve those notices and evaluate applicable third-party component terms; this
is positive license evidence, not a reason to request a blanket new SDK permission.

The retained iWave U-Boot recipe identifies revision
`b68af07c82eb869537862fc59478f73b0187762e` and twelve patches. It requires the pinned
NXP U-Boot recipe. Upstream [U-Boot's v2025.04 license explanation](https://github.com/u-boot/u-boot/blob/v2025.04/Licenses/README)
describes GPLv2, with later-version choice for most files and per-file exceptions.
A complete binary image must account for its actual U-Boot/SPL source and scripts,
not only the Linux source archive. Boot containers can also include independently
licensed firmware, so their contents require a component map.

### Immediate release inventory

This table is the bounded release assessment, not a complete rootfs SBOM.
Machine-readable details are in the profile's `redistribution-inventory.json`.

| Component | Role | Included / public download / vendor supplied | Actual remaining evidence or terms | Supplier and next action |
|---|---|---|---|---|
| Linux core, in-tree drivers, DT and our extensions | Installed kernel and ordinary modules | Published source-only kernel-core archive plus public extension source/licenses | Keep the exact modified preferred source, config and required scripts paired to the binary release; no extra GPL permission or identical-hash rebuild condition | Project release owner: maintain complete scoped source delivery and notices |
| Five factory external modules | Crypto, UIO/noncached-memory and WLAN modules preserved in the kernel set | Vendor binaries local; three pinned public GPL source candidates/recipes are identified | Actual source/build mapping and presence/absence of additional changes are UNKNOWN, not five proved unavailable source projects | iWave build/BSP maintainer: confirm actual source revisions, effective recipe/patch set and required generated/config inputs |
| U-Boot/SPL and recovery Linux/userspace | Boot containers and flashing environment | Vendor flash inputs local; U-Boot revision and twelve supplied patches recorded | Complete source/config/scripts for the actual distributed GPL components and a container component map are not assembled in the kernel-core release | iWave BSP maintainer and project release owner: assemble boot/recovery corresponding source |
| `libTelematics.so` variants and API headers | Factory monitor/peripheral userspace support | Two supplied library variants; headers remain outside the public source export | No redistribution grant or prohibition established in the inspected directory/package scope; variant-specific terms UNKNOWN | iWave library/licensing contact: provide existing applicable terms and identify the intended redistributable variant |
| Radio, DDR/boot and other firmware blobs actually included | Device/boot firmware | Vendor supplied, potentially mixed-license; exact image inventory must identify each file | Per-file matching license/EULA/notice and redistribution conditions not established in this review | iWave BSP maintainer: file/package/license mapping; apply existing NXP/radio-vendor grants where present |
| Rootfs packages | System utilities, libraries and services | Stock inventory now lists 2,720 installed packages / 575 OE recipes; recovery files are separate | 0 declared License and 0 Source fields; 2,720 metadata license fields UNKNOWN; literal license manifests absent. Recovery and unpackaged files remain separate | iWave release engineer supplies the exact recipe/version license/source map; project distributor fulfills applicable GPL/LGPL/relinking and notice terms |
| .NET SDK and third-party components | Managed runtime and SDK payload | Pinned Microsoft download, local MIT license and third-party notices present | Retain notices and map any component-specific conditions for the SDK actually shipped | Project distributor: include the supplied license/notices and their applicable obligations |

The [owner-ready vendor request](vendor-source-request.md) asks for the specific
missing build records/source and separately licensed binary terms. It has not been
sent. Its GPL section requests fulfillment/information under existing licenses,
not permission to exercise rights already granted.

## Partial public kernel-core binary scope

The kernel-core delivery contains Image/DTB, 807 native and two upstream IMU
modules (809 total), their metadata/notices and exact preferred-source references.
It excludes the five factory-module binaries, stock rootfs, boot/recovery binaries,
SDK and application. The checked release configuration uses empty
`CONFIG_EXTRA_FIRMWARE` and `CONFIG_INITRAMFS_SOURCE`. Its full native build,
static qualification and actual BSP-restored neutral-image composition passed;
physical target checks remain outside that result. The release descriptor and
`build-verification.json` identify the delivered binaries and accepted scope.

This bounded GPL binary release may satisfy its own source/notice obligations
without waiting for rights/source questions about excluded binaries. GPL supplies
its redistribution rights; neither additional iWave consent nor a bit-identical
rebuild is an extra license requirement. Recipients who combine the core with their
own exact BSP regain the full 814-module image scope and its independent module,
rootfs/boot/recovery, library and firmware obligations. The
[assembly workflow](assemble-with-kernel-core.md) distinguishes those suppliers and
uses a trusted descriptor instead of guessing archive names or hashes.

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
modules or the whole device image. Likewise, a technical image PASS does not determine separately licensed binary terms
or fulfill other rootfs packages' license obligations. A complete-image assessment
includes recovery binaries distributed with the bundle, even if they are temporary
flashing tools rather than the final installed root filesystem.

## Application separation

Normal userspace use of the kernel's syscall/UAPI boundary does not by itself
require publishing an entire unrelated application. Application signing keys,
service authority and installation secrets are not public kernel source. An
application-specific image still needs its own component inventory and source
compliance process for the components it actually distributes.

## References

The local Linux `COPYING` and `LICENSES/preferred/GPL-2.0` provide the actual license
text; web references explain it and are not a substitute for shipping required notices.

- [Linux kernel licensing rules](https://docs.kernel.org/process/license-rules.html)
- [GPLv2 text](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)
- [GNU licensing FAQ](https://www.gnu.org/licenses/gpl-faq.html)
- [GitHub large-file/release guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
