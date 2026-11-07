# Kernel-core source delivery

The source-only archive contains the actual preferred editable Linux working tree,
including the applied vendor changes and nonignored added Device Tree and driver
sources. A plain `git archive HEAD` is insufficient: HEAD is the vendor base commit,
while the qualified build also uses local modifications and added files.

This package is explicitly **incomplete for the five retained factory modules**:
`cryptodev.ko`, `igb_uio.ko`, `kpage_ncache.ko`, `mlan.ko` and `moal.ko`. Their candidate
source mapping and unresolved correspondence are recorded in the included
`extensions/profiles/tch-g62h-imx91-rel3.3/source-gaps.json`. The package does not
contain those binary modules or claim to be complete corresponding source for all
814 modules in the local firmware. A source-only publication does not clear the
vendor proprietary library, firmware or complete binary image for redistribution.

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
build_root=/absolute/path/to/kernel-build
mkdir -p "$build_root"
cp qualification/kernel.config "$build_root/.config"
timeout --kill-after=30s 10800s make -C "$source_root" O="$build_root" \
  ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  CC=aarch64-linux-gnu-gcc-14 HOSTCC=gcc \
  KERNELRELEASE=6.12.34-omam-rel33-policy1 -j2 \
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
  KERNELRELEASE=6.12.34-omam-rel33-policy1 M="$imu_root" modules
```

On a shared host, reserve the CPU/memory scope described by the public kernel
tooling before running these bounded commands. `-j2` limits parallel make jobs;
it is not a memory or CPU-time reservation. Build outputs are new local work and
must retain the free-space reserve.

`extensions/tools/kernel/qualify.py` records the actual selected input hashes,
patch invocation, Makefile, stripping and static ABI checks used by the maintained
qualification route. That full route also checks the five retained stock modules;
those modules and their acquisition/build inputs are deliberately not supplied
by this source-only package. Rebuilding the core and ST modules therefore does
not close the five-module source gap or qualify a replacement complete firmware.

Do not flash an unqualified build. Boot, CAN, interrupt delivery, energy/wake and
IMU behavior require separate acceptance on the target hardware. Source packaging
and its host tests perform no device operations and do not establish those results.
