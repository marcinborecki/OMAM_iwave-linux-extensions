# Kernel-core delivery, assembly and native ARM64 rebuild tooling

These public Python tools prepare, build and statically qualify the Telematics
Connect Hub G62H i.MX91 REL3.3 Linux layer. The source-release path works with an
extracted preferred-source snapshot, without `.git`, an OMAM checkout or a private
source/build receipt. All board inputs, compiler, work, output and receipt paths
are explicit. The tools perform no device or flash operation.

This route compiles the kernel, DTB and native modules, preserves five exact stock
external modules and builds the patched upstream IMU pair. The resulting 814-module
set can be bound to an explicit local image policy and composed with the supplied
stock rootfs and pinned .NET SDK. It is a usable build route with exact BSP inputs,
not a claim that a Git clone contains an entire Yocto/bootloader/rootfs delivery.

## Optional fast assembly using the public kernel core

The `v0.2.0-kernel-core` delivery supplies Image, normal DTB and **809 module
binaries**: 807 native plus two IMU. It excludes five factory-module binaries,
stock rootfs, boot/recovery files, SDK and private application. The full native
build, static 814-module qualification and real restored-core-to-neutral-image
assembly passed on ARM64 Ubuntu 26.04. Physical board acceptance remains separate.
Use `profiles/tch-g62h-imx91-rel3.3/kernel-core-release.json` for the asset URL,
filename, byte count, SHA-256 and delivery identity. `build-verification.json` in
that profile records the exact accepted scope and output hashes.

The fast path uses Linux, Python 3.12+, zstd and GNU tar, without compiling a kernel
or executing the target ARM64 SDK. The customer supplies the exact iWave BSP; Microsoft
supplies the SDK via the existing public lock. Public core source/notice obligations
are separate from the omitted factory-module and full-image terms. No additional
iWave consent or bit-identical rebuild is required to exercise existing GPL rights.

From the current public repository root, choose a new assembly workspace and the
exact vendor delivery. Set these paths once; subsequent blocks use the same values:

```sh
project_root="$PWD"
assembly_root=/absolute/path/to/new/tch-core-assembly
vendor_root=/absolute/path/to/the/supplied/REL3.3/delivery
baseline_root="$assembly_root/bsp-baseline"
core_extract="$assembly_root/core-extracted"
bundle_root="$core_extract/iwave-kernel-core"
release_record="$project_root/profiles/tch-g62h-imx91-rel3.3/kernel-core-release.json"
sdk_root="$assembly_root/dotnet-11.0.100-linux-arm64"
local_policy="$assembly_root/local-image-policy.json"
image_root="$assembly_root/neutral-base"
export assembly_root release_record
mkdir -p "$assembly_root/downloads"
```

Read the asset's name, URL, size and SHA-256 from the **trusted release record**.
This bounded downloader verifies an existing cache or a new download before returning;
it never guesses a release filename/hash and never overwrites a different cache:

```sh
python3 -B - <<'FETCH_CORE'
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

record_path = Path(os.environ['release_record'])
if not record_path.is_file():
    raise SystemExit('Kernel-core release descriptor is not available in this checkout yet')
record = json.loads(record_path.read_text())
if record.get('schema') != 'iwave.kernel-core-release.v1':
    raise SystemExit('Unexpected kernel-core release descriptor schema')
asset = record['archive']
name, size, expected, url = (asset[key] for key in ('name', 'bytes', 'sha256', 'url'))
if (Path(name).name != name or not name or type(size) is not int or size <= 0
        or not re.fullmatch(r'[0-9a-f]{64}', expected)
        or urlsplit(url).scheme != 'https' or urlsplit(url).hostname != 'github.com'):
    raise SystemExit('Unsafe or incomplete asset descriptor')
folder = Path(os.environ['assembly_root']) / 'downloads'
folder.mkdir(parents=True, exist_ok=True)
target = folder / name
created = False
try:
    if not target.exists():
        if shutil.disk_usage(folder).free < size + 500 * 1024**2:
            raise SystemExit('Need the asset size plus 500 MiB free reserve')
        deadline = time.monotonic() + 900
        total = 0
        with target.open('xb') as output:
            created = True
            with urlopen(url, timeout=60) as incoming:
                while block := incoming.read(1024 * 1024):
                    total += len(block)
                    if total > size or time.monotonic() > deadline:
                        raise RuntimeError('Download exceeded its declared size or deadline')
                    output.write(block)
    if target.is_symlink() or not target.is_file() or target.stat().st_size != size:
        raise RuntimeError('Kernel-core archive size/type mismatch')
    with target.open('rb') as content:
        actual = hashlib.file_digest(content, 'sha256').hexdigest()
    if actual != expected:
        raise RuntimeError('Kernel-core archive SHA-256 mismatch')
except BaseException:
    if created:
        target.unlink(missing_ok=True)
    raise
print('Verified archive:', target)
FETCH_CORE
```

Extract only that verified archive into a new directory. The descriptor is read
again to obtain its exact filename; the shell variable is quoted:

```sh
core_name="$(python3 -B - "$release_record" <<'ASSET_NAME'
import json
from pathlib import Path
import sys
name = json.loads(Path(sys.argv[1]).read_text())['archive']['name']
if not name or Path(name).name != name:
    raise SystemExit('Unsafe archive filename')
print(name)
ASSET_NAME
)"
mkdir "$core_extract"
tar --zstd --no-same-owner --same-permissions \
  -xf "$assembly_root/downloads/$core_name" -C "$core_extract"
```

Prepare the board-specific baseline **without a compiler**, then restore only the
five exact omitted factory modules from that supplied BSP:

```sh
python3 -B tools/kernel/prepare.py --vendor-root "$vendor_root" --output "$baseline_root"
python3 -B tools/kernel/core_bundle.py restore --bundle-root "$bundle_root" \
  --baseline "$baseline_root" --vendor-root "$vendor_root" --release-record "$release_record"
```

The restored kernel directory is `$bundle_root/kernel` and its receipt is
`$bundle_root/kernel-build.json`. Restore verifies the trusted delivery record,
public files and factory-module identities, then runs the actual full 814-module
kernel-set validator. A renamed or approximate module is not a substitute.

Stage the pinned Microsoft SDK, generate a distinct local candidate policy and
validate composition inputs. These operations do not execute the ARM64 SDK on the
host and do not compile the kernel:

```sh
python3 -B tools/image/sdk_payload.py stage \
  --manifest profiles/tch-g62h-imx91-rel3.3/dotnet-sdk.lock.json \
  --cache-dir "$assembly_root/downloads/dotnet" --output-dir "$sdk_root"
python3 -B tools/image/qualify_profile.py --vendor-root "$vendor_root" \
  --vendor-manifest "$baseline_root/inputs/manifest.json" \
  --kernel-root "$bundle_root/kernel" --kernel-receipt "$bundle_root/kernel-build.json" \
  --sdk "$sdk_root" --output "$local_policy"
python3 -B tools/image/build_base.py --profile tch-g62h-imx91-rel3.3 \
  --vendor-root "$vendor_root" --vendor-manifest "$baseline_root/inputs/manifest.json" \
  --kernel-root "$bundle_root/kernel" --kernel-receipt "$bundle_root/kernel-build.json" \
  --sdk "$sdk_root" --image-policy "$local_policy" --output "$image_root" --dry-run
```

After accepting that dry-run selection and its output/scratch allowance, compose
and independently validate the new neutral base using that same explicit policy:

```sh
python3 -B tools/image/build_base.py --profile tch-g62h-imx91-rel3.3 \
  --vendor-root "$vendor_root" --vendor-manifest "$baseline_root/inputs/manifest.json" \
  --kernel-root "$bundle_root/kernel" --kernel-receipt "$bundle_root/kernel-build.json" \
  --sdk "$sdk_root" --image-policy "$local_policy" --output "$image_root"
python3 -B - "$image_root" "$local_policy" <<'CHECK_IMAGE'
from pathlib import Path
import sys
sys.path.insert(0, str(Path('tools/image').resolve()))
from build_base import validate_base
validate_base(Path(sys.argv[1]), policy_path=Path(sys.argv[2]))
print('Validated neutral image against the selected local policy')
CHECK_IMAGE
```

Keep the maintained default image policy unchanged. A successful local policy/base
check establishes the recorded software/archive boundary, not physical boot or CAN,
IMU, power/current/wake acceptance. Each new output path must be absent. An existing
SDK/base is reused by its verified identity rather than overwritten to follow an
example. Preserve at least 500 MiB free beyond outputs on each involved filesystem.

The detailed assembly diagram and scope are recorded in
`docs/assemble-with-kernel-core.md`. This README includes the essential commands
above and does not require that document to run the scoped assembly flow. The full
preferred-source rebuild route continues below. `prepare.py` without `--toolchain`
creates BSP-only assembly inputs; compilation additionally requires the pinned
compiler and the toolchain-bearing baseline described below.

## Public inputs and supplied board inputs

Use the **current Git checkout's** tools and profile locks. The source archive's
`extensions/` folder preserves a historical support snapshot and may predate the
current preparation and `--source-manifest` route.

| Input | Acquisition and role |
|---|---|
| Preferred Linux tree and source manifest | Public asset `kernel-core-6.12.34-tch-rel33.tar.zst`, named by `profiles/tch-g62h-imx91-rel3.3/source-release.json`; verify its recorded size and SHA256 before extraction. |
| Compiler prefix | Nine official Ubuntu package URLs with exact size/SHA256 in `build-toolchain.json`; extract each verified `.deb` using `dpkg-deb -x` into one new prefix. |
| Board BSP | User supplies the exact REL3.3 directory containing `Firmware/`, `Linux/` and `iG-PRGTT-TelematicsConnectHub.uuu`; `vendor-files.lock.json` binds all nine required files. The project does not provide a public download of those binaries. |
| Baseline metadata | `prepare.py` generates it from the supplied BSP and pinned compiler. It records native module names and retains only five external module binaries. |
| SDK for later image composition | The Microsoft URL/SHA512 in `dotnet-sdk.lock.json` and `tools/image/sdk_payload.py stage` produce the required staged payload/report. |

The published archive is 230,766,173 bytes; the bound source manifest is
18,432,045 bytes with SHA256
`11d819b4da74eff7ed8cd81517c739d20f08cf69c42b1364fab4e5608a920d9e`.
The nine compiler packages total 35,609,694 downloaded bytes. Download the exact
URLs in the JSON locks and verify each size/hash, rather than substituting a
same-named package from another distribution revision. Reusing an existing cached
file also requires its successful verification. Extract into new directories and
preserve source file modes and symlinks; for GNU tar use `--no-same-owner` and
`--same-permissions` with the already verified source archive.

The exercised build host is native ARM64 Ubuntu 26.04 LTS. Use a compatible
native ARM64 Linux/Ubuntu host with Python 3.12+, Git, GNU tar,
zstd, timeout, dpkg-deb, make, binutils, a host C compiler, kmod/depmod, patch and
normal Linux development prerequisites, including libc/OpenSSL/ELF support. The
pinned target compiler is `aarch64-linux-gnu-gcc-14 (Ubuntu 14.3.0-14ubuntu1) 14.3.0`.
The extracted package prefix still depends on a compatible host userspace.

For a conventional input layout, run commands from the repository root and set:

```sh
project_root="$PWD"
source_root="$project_root/.local/source-release/iwave-linux-source/linux"
source_manifest="$project_root/.local/source-release/iwave-linux-source/source-manifest.json"
toolchain_root="$project_root/.local/toolchain"
baseline_root="$project_root/.local/rebuild-inputs"
vendor_root=/absolute/path/to/the/supplied/REL3.3/delivery
work_root=/absolute/path/to/new/kernel-work
kernel_root="$project_root/.local/rebuilt-kernel"
kernel_receipt="$project_root/.local/rebuilt-kernel-build.json"
release_name=6.12.34-iwave-rel33-rebuild1
```

Keep `work_root` outside the source tree. Input, work and image-output filesystems
may be separate. The source archive has 1,531,588,302 logical regular-file bytes
before filesystem overhead. The retained toolchain prefix has 115,518,770 logical
regular-file bytes. Build admission requires another 3 GiB plus 500 MiB reserve;
qualification needs another 600 MiB free plus the reserve while work outputs remain.
These allowances are additional to existing BSP/source/SDK inputs. Check each
filesystem and preserve the reserve throughout the run.

## Prepare and verify the source

```sh
python3 -B tools/kernel/prepare.py \
  --vendor-root "$vendor_root" --toolchain "$toolchain_root" \
  --output "$baseline_root"
python3 -B tools/kernel/apply.py --source "$source_root" --with-energy --check
```

Preparation validates the vendor-file lock and compiler hash, extracts stock
IKCONFIG, records all 807 native module names and extracts the five exact factory
external binaries. It writes `inputs/stock.config`, `inputs/manifest.json`, the
ordered vendor-patch references, `toolchain/manifest.json` and
`baseline/stock-external-modules.json`. Its output must be absent. Preparation's
storage admission is 32 MiB plus the reserve; it streams the stock rootfs instead
of extracting a second complete filesystem.

The public source snapshot already contains all fifteen ordered vendor patches
and the FlexCAN/BQ25601 changes. `apply.py --check` validates all seven FlexCAN
results and the complete charger driver against their pinned manifests. It refuses
unknown or partially applied sources. For a separately prepared vendor Git tree,
the same tool without `--check` can apply the exact matching overlays; do not
reapply vendor changes to the released snapshot.

`--source-manifest` explicitly selects the published snapshot route. Its manifest
size/SHA256 are checked against the current profile, then every declared Linux
file's actual bytes, mode, type and symlink target are verified. Additional source
files, missing files, unsafe links and special entries are refused. Root `.git`
metadata is not read. Omitting the option retains the legacy exact Git revision,
diff and added-file identity route; the tools do not silently switch between them.

## Resource-bounded compilation

Choose CPU numbers actually reserved for your run. `1,2` below is an example, not
a machine-wide default. The scope requires CPU affinity, at most 150% CPU, 6 GiB
memory, no swap, two make jobs and nice 10. Resource limits describe the build host,
not settings for the target board. Keep source, manifest, baseline, toolchain,
work directory and release identical through compilation and qualification.

```sh
build_cpus=1,2
systemd-run --user --scope --collect --unit=iwave-kernel-full \
  -p CPUQuota=150% -p AllowedCPUs="$build_cpus" \
  -p MemoryMax=6G -p MemorySwapMax=0 -p RuntimeMaxSec=11200 \
  taskset -c "$build_cpus" nice -n 10 python3 -B tools/kernel/build.py \
  --allowed-cpus "$build_cpus" --cpu-percent 150 --memory-gib 6 --jobs 2 --nice-min 10 \
  --source "$source_root" --source-manifest "$source_manifest" \
  --baseline "$baseline_root" --toolchain "$toolchain_root" --work "$work_root" \
  --output "$kernel_root" --receipt "$kernel_receipt" \
  --release "$release_name" --phase full
```

`--phase driver` is available for a focused FlexCAN/charger compilation before the
full phase; reuse the same owned work/receipt and release. The full make is bounded
to 10800 seconds, with a separate bounded `olddefconfig` when first preparing work.
Owned subprocess groups are stopped on interruption or timeout. A failed build
retains its work and receipt for diagnosis; it is not a successful qualification.

The work uses stock configuration after `olddefconfig`, the exact chosen
`KERNELRELEASE` and source-prefix mapping. Repeated byte-identical output is not
claimed: compiler/build-environment metadata can change bytes. GPL source
completeness and binary reproducibility are different questions. Qualification
must assess the new outputs instead of treating a historical Image hash as proof
of the new build.

## Qualify the full module set

After the full build reports completion, run the qualifier with the same inputs
and a new `kernel_root`. Do not pre-create that output directory.

```sh
systemd-run --user --scope --collect --unit=iwave-kernel-qualify \
  -p CPUQuota=150% -p AllowedCPUs="$build_cpus" \
  -p MemoryMax=6G -p MemorySwapMax=0 -p RuntimeMaxSec=2400 \
  taskset -c "$build_cpus" nice -n 10 python3 -B tools/kernel/qualify.py \
  --allowed-cpus "$build_cpus" --cpu-percent 150 --memory-gib 6 --jobs 2 --nice-min 10 \
  --source "$source_root" --source-manifest "$source_manifest" \
  --baseline "$baseline_root" --toolchain "$toolchain_root" --work "$work_root" \
  --output "$kernel_root" --receipt "$kernel_receipt"
```

Qualification performs these connected checks:

1. Install the rebuilt native modules and compare their complete names/count to
   the source-bound compact stock inventory. The legacy baseline module-tree
   fallback remains supported when the compact list is absent.
2. Preserve the five exact stock external binaries and build ST LSM6DSX core/I2C
   modules against the candidate headers/symbol versions, applying the IRQ patch
   to a small separate source directory with zero fuzz.
3. Check Image banner and embedded configuration, then independently compare
   parsed Image exports with `Module.symvers`.
4. Check symbol CRCs, namespaces, GPL-only imports, vermagic, module layout, ARM64
   relocations, dependencies and DTB CAN/IMU identities.
5. Write exact output/module/index manifests and verify the two-module IMU package.

The pure Image parser supports the pinned ARM64 Linux 6.12 PREL32 layout. The
loader parser checks actual ELF contracts rather than relying on release text.
Retained `omam_` symbols, manifest formats and `updates/omam` names are compatibility
identities, not an application or deployment configuration.

## Hand the rebuilt kernel to the image builder

Stage the public SDK lock into a new directory and generate an explicit local
policy from the newly qualified set:

```sh
mkdir -p .local/sdk
python3 -B tools/image/sdk_payload.py stage \
  --manifest profiles/tch-g62h-imx91-rel3.3/dotnet-sdk.lock.json \
  --cache-dir .local/downloads/dotnet \
  --output-dir .local/sdk/dotnet-11.0.100-linux-arm64
sdk_root="$project_root/.local/sdk/dotnet-11.0.100-linux-arm64"
local_policy="$project_root/.local/rebuilt-image-policy.json"
python3 -B tools/image/qualify_profile.py \
  --vendor-root "$vendor_root" --vendor-manifest "$baseline_root/inputs/manifest.json" \
  --kernel-root "$kernel_root" --kernel-receipt "$kernel_receipt" \
  --sdk "$sdk_root" --output "$local_policy"
mkdir -p artifacts
image_root="$project_root/artifacts/my-rebuilt-neutral-base"
python3 -B tools/image/build_base.py \
  --profile tch-g62h-imx91-rel3.3 --image-policy "$local_policy" \
  --vendor-root "$vendor_root" --vendor-manifest "$baseline_root/inputs/manifest.json" \
  --kernel-root "$kernel_root" --kernel-receipt "$kernel_receipt" \
  --sdk "$sdk_root" --scratch-root /var/tmp --output "$image_root" --dry-run
```

The dry run checks actual inputs and output/scratch admission. After success,
repeat the same command without `--dry-run`. Keep the 500 MiB free reserve beyond
its computed outputs. Composition writes the neutral rootfs, required boot files
and a base manifest; it does not flash them. The default maintained profile remains
unchanged. A new candidate must use its explicitly trusted local policy:

```sh
python3 -B - "$image_root" "$local_policy" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path('tools/image').resolve()))
from build_base import validate_base
validate_base(Path(sys.argv[1]), policy_path=Path(sys.argv[2]))
print('PASS: actual image directory matches the explicitly selected policy')
PY
```

A policy delivered by an untrusted image supplier is not automatically trusted.
A successful `PASS_BUILD_AND_STATIC_ABI` or archive check establishes its declared
software boundary. Actual boot, interrupts, CAN traffic, IMU samples, physical
current and wake behavior require separate target acceptance. Local use of the
supplied BSP and public sources does not close whole-image source/redistribution
obligations for retained modules, boot software, libraries or firmware.

## Host regression checks

From the repository root:

```sh
timeout --kill-after=5s 120s sh tests/native/flexcan/run.sh
timeout --kill-after=5s 120s python3 -B -m unittest discover -s tests/native/flexcan -p 'test_*.py' -v
timeout --kill-after=5s 120s python3 -B -m unittest discover -s tests/python -p 'test_kernel_*.py' -v
python3 -B tests/native/flexcan/patch_test.py --source "$source_root"
python3 -B tests/native/flexcan/run_rx_busy.py --kernel-source "$source_root"
```

These tests exercise independent register/acceptance expectations, ELF/loader
errors, changed package bytes, extra payload, symlinks, source-manifest boundaries
and resource admission. They do not open a CAN socket, load a module or operate a
device. Their passing result is separate from a completed full build and image
acceptance. The actual new build's result belongs in its receipt/profile record.
