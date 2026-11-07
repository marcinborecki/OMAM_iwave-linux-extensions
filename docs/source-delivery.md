# Kernel-core source delivery

The source-only archive supplies the preferred editable Linux tree, including the
applied vendor changes, added Device Tree/driver sources, configuration and source
license notices. GPL-covered code can be published under its existing license
without an additional iWave permission. Adjacent proprietary libraries and firmware
retain their own terms and are assessed separately.

The current archive's declared scope excludes the five retained factory modules:
`cryptodev.ko`, `igb_uio.ko`, `kpage_ncache.ko`, `mlan.ko` and `moal.ko`. Public GPL
source candidates exist for all five through three pinned source projects. What
remains UNKNOWN is whether those candidates include the actual factory build's
complete effective changes/configuration and required generated/staged inputs.
This is not a finding that five source projects are unavailable or that local
vendor patches are proved missing.

The included `extensions/profiles/tch-g62h-imx91-rel3.3/source-gaps.json` records
that boundary. This archive is not a claim of complete corresponding source for all
814 local modules, U-Boot/recovery software or an entire rootfs. It contains no
factory external-module binaries. Complete-image obligations and separately
licensed blob terms are itemized in [licensing-and-sources.md](licensing-and-sources.md).

A plain archive of the vendor Git HEAD omits the working tree's actual changes and
added source files. The packager includes those preferred sources. Its source/binary
hashes establish identity and provenance; **GPL does not require a rebuild to produce
identical binary hashes**. The [FSF exact-hash FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#MustSourceBuildToMatchExactHashOfBinary)
explains that distinction. Corresponding source still means the source used for the
version distributed, with the applicable build/install inputs.

## Use the released sources with the current public tools

The preferred-source archive is a source snapshot, not a full firmware download.
Its `extensions/` directory preserves the support files present when that snapshot
was packaged. Use the **current Git checkout's** tools and profile locks for the
maintained complete local build route. Do not run old archived scripts expecting
them to know the later bootstrap or candidate-policy interface.

The current route needs no `.git` directory inside the extracted Linux tree.
`--source-manifest` in both build and qualification checks the published manifest
pin, all Linux file bytes/modes/types/links and the complete selected inventory.
The current `source-release.json` binds the 18,432,045-byte manifest to SHA256
`11d819b4da74eff7ed8cd81517c739d20f08cf69c42b1364fab4e5608a920d9e`.
Legacy Git-source identity remains a separate explicit route.

For a complete local neutral image, supply the exact REL3.3 BSP, fetch the nine
pinned public Ubuntu toolchain packages and stage the pinned Microsoft SDK. Then
run current `tools/kernel/prepare.py`, compile, qualify the full module set, generate
an explicit candidate policy with `tools/image/qualify_profile.py`, compose with
`build_base.py --image-policy`, and call `validate_base(..., policy_path=...)` with
that trusted policy. Preparation generates the technical baseline receipts from
BSP/compiler inputs rather than requiring a prior private checkout or receipt.

The full commands, verified acquisition steps, storage admissions and supplier
boundaries are in [build-from-source.md](build-from-source.md). This is a
source-plus-supplied-BSP route. Boot/recovery binaries and the stock userspace
remain supplied board inputs; it does not rebuild the entire Yocto delivery from
source. Compilation, static qualification, archive acceptance, physical board
operation and corresponding-source completeness remain distinct results.

## Create the archive

Run on Linux with Python 3, Git, zstd and the exact prepared source/kernel inputs.
No network access, installed device, application package or private image profile
is used. From the public project root:

```sh
python3 tools/source/package.py \
  --source .local/sources/linux \
  --kernel-set .local/kernel-set \
  --output /tmp/iwave-tch-g62h-rel33-kernel-core-source.tar.zst \
  --max-output-bytes 1073741824
```

`--source`, `--kernel-set` and `--output` are explicit inputs. `--project` defaults
to the public project containing the tool. Use a new output path with an existing
parent. Admission requires the declared compressed-output bound plus 500 MiB free.
The compressor also has a file-size limit, one zstd thread and a bounded deadline.
The tool streams files directly into tar/zstd without extracting a rootfs or
copying the source tree. Failed or interrupted attempts remove their owned partial
archive and stop their compressor process group. An existing output is never replaced.

Add `--dry-run` to validate actual source/configuration identities and the storage
admission without creating an archive. A dry run is not a completed source release.

The packager checks the source revision, Git diff fingerprint and added-file
hashes against the qualified kernel manifest, then checks the actual kernel
configuration against its declaration. It does not copy the raw build receipt,
Git configuration, private acquisition paths or binary kernel set. The identity
summary contains only the public release, output/module sizes and hashes and
explicit source gaps. No toolchain binary or SDK is included.

The selected source inventory is tracked files plus nonignored added files from
the actual working tree. Deleted tracked files remain absent. Git-ignored build outputs, Git metadata, caches and explicit local/private input
paths are excluded. Normal source-controlled fixtures and data remain included,
including binary test inputs; no extension or text-content filter removes them. Source symlinks and executable modes are preserved; symlinks escaping
the selected tree are refused. Original copyright, licensing and source notices
remain in their files. The public components, profiles, tools, tests, licenses,
NOTICE and this instruction are included separately from the Linux tree.

The archive root is `iwave-linux-source/`:

```text
linux/                          actual modified Linux source
extensions/                     public extensions and portable recipes
qualification/kernel.config     actual qualified configuration
qualification/kernel-identities.json
source-manifest.json            source identity and per-file hashes
```

The embedded manifest lists each delivered source/support file's SHA256, size,
mode and symlink target where applicable. It excludes its own self-referential
hash. The command reports the whole compressed archive's SHA256 and size for a
release checksum. Source files are checked while streaming and the selected input
graph is checked again before publishing the completed local output. Keep inputs
stable for the command; do not edit or build in that source tree concurrently.

## Rebuild the kernel core without private acquisition receipts

This core-only compilation uses public Linux source, configuration and a suitable
compiler. It does not require a vendor login, factory module binaries or a BSP
rootfs. Those are inputs to the separate complete-image/restoration workflow.
For the v0.2.0 core delivery, use release `6.12.34-iwave-rel33-policy1`; the
configuration shipped in `kernel/config` has the same SHA-256 as the source
archive's `qualification/kernel.config`. A different build environment can change
binary bytes without changing which preferred sources were used.


Extract the archive into a fresh build workspace. Use an ARM64-capable GCC 14
compiler plus the normal Linux build prerequisites, including make, binutils,
bison, flex, OpenSSL development headers and ELF development headers. The accepted
build used `aarch64-linux-gnu-gcc-14 (Ubuntu 14.3.0-14ubuntu1) 14.3.0`;
the profile's build-toolchain.json records package versions/hashes and compiler identity; using a
different compiler is a new build requiring validation. These commands describe
the kernel-core build and do not promise byte-for-byte binary reproducibility.
From `iwave-linux-source/`, with your build directory outside `linux/`:

```sh
source_root="$PWD/linux"
kernel_release=6.12.34-iwave-rel33-policy1
build_root=/absolute/path/to/kernel-build
mkdir -p "$build_root"
cp qualification/kernel.config "$build_root/.config"
timeout --kill-after=30s 10800s make -C "$source_root" O="$build_root" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CC=aarch64-linux-gnu-gcc-14 HOSTCC=gcc \
  KERNELRELEASE="$kernel_release" -j2 \
  Image freescale/imx91-iwg62h-prgtt.dtb modules
```

The configuration and this exact source snapshot already include the vendor,
FlexCAN and BQ25601 changes. Do not reapply those overlays to an already patched
file. Review the resulting configuration and warnings. Kernel Image, DTB and
in-tree modules must be qualified together against the chosen toolchain.

The two upstream ST LSM6DSX modules follow a separate external-module recipe.
Their editable upstream C/header sources are already under
`linux/drivers/iio/imu/st_lsm6dsx/`. Copy only those source files to a small external
module directory, then apply the existing-IRQ patch there with zero fuzz:

```sh
imu_root=/absolute/path/to/imu-build
mkdir -p "$imu_root"
cp "$source_root"/drivers/iio/imu/st_lsm6dsx/*.[ch] "$imu_root/"
patch --batch --forward --fuzz=0 --no-backup-if-mismatch \
  -d "$imu_root" -p5 \
  -i "$PWD/extensions/components/st-lsm6dsx-existing-irq/omam-st_lsm6dsx_i2c-existing-irq.patch"
cat > "$imu_root/Makefile" <<'MAKEFILE'
st_lsm6dsx-y := st_lsm6dsx_core.o st_lsm6dsx_buffer.o st_lsm6dsx_shub.o
obj-m += st_lsm6dsx.o st_lsm6dsx_i2c.o
MAKEFILE
timeout --kill-after=30s 10800s make -C "$source_root" O="$build_root" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CC=aarch64-linux-gnu-gcc-14 HOSTCC=gcc \
  KERNELRELEASE="$kernel_release" M="$imu_root" modules
```

On a shared host, reserve the CPU/memory scope described by the public kernel
tooling before running these bounded commands. `-j2` limits parallel make jobs;
it is not a memory or CPU-time reservation. Build outputs are new local work and
must retain the free-space reserve.

The archive's `extensions/tools/kernel/qualify.py` preserves the selected-input,
patch, Makefile, stripping and static ABI recipe at that source snapshot. Use
`tools/kernel/qualify.py` from the current Git checkout for the maintained route.
It also checks the five retained stock modules, which current preparation obtains
from a separately supplied exact BSP rootfs. Those factory binaries and the full
board delivery are deliberately not supplied by this source-only package.
Rebuilding the core and ST modules therefore does not alone close the five-module
source gap or qualify a replacement complete firmware.

Do not flash an unqualified build. Boot, CAN, interrupt delivery, energy/wake and
IMU behavior require separate acceptance on the target hardware. Source packaging
and its host tests perform no device operations and do not establish those results.

## Binary recipients and source completeness

For a distributed GPLv2 kernel/module binary, select and fulfill a GPLv2 section 3
source route for that release. A partial kernel-core source release and candidate
upstream URLs do not automatically fulfill the obligation for omitted factory
modules. Recipient access to corresponding source must not depend on a private
application checkout or discretionary vendor login approval. Public source hosting
is a delivery choice; GPL rights themselves do not require a separate iWave consent.

Do not confuse this with reproducible-build acceptance. Toolchain/timestamp changes
can alter output bytes even with corresponding source. Obtain actual source/build
records, needed patches or affirmative no-extra-change confirmation, configuration,
interface definitions and controlling scripts. An exact binary hash comparison can
provide useful evidence, but is not the only permissible proof or an extra GPL rule.

A whole-image delivery adds the actual bootloader/SPL, recovery kernel/initramfs and
rootfs component obligations. Independent library/firmware redistribution terms must
be identified per component. UNKNOWN terms are an unresolved evidence item, not a
statement that distribution is forbidden or that all GPL source needs new permission.
The profile's redistribution inventory and the prepared vendor request identify
concrete suppliers/actions; neither sends a message nor introduces a blanket approval
gate. Private keys and installation credentials are not public corresponding source.
