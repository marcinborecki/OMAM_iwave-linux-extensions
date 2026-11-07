# Pinned native ARM64 kernel tooling

These standalone Python tools prepare, build and statically qualify the first iWave
Connect Hub G62H i.MX91 REL3.3 profile. They require Python 3.11 or later. No OMAM
application checkout, installer or private profile is imported. All source, toolchain,
work, receipt and output paths are explicit. They do not contact or flash a device.

## Source preparation

`apply.py` validates `components/flexcan-controller-policy/manifest.json`, every
existing base hash and every new source overlay before changing the target Linux
source. Exact already-applied results are a no-op. Unknown or partially applied input
is refused. `--with-energy` also applies the pinned BQ25601 change from
`components/bq25601-poweroff/source.json`. Both routes validate full resulting hashes.
The upstream base is `02cf250c2e6bd375c11dae324ea145a39015d41b` with all 15 supplied
REL3.3 vendor patches in recipe order. A different upstream tree is not supported.

```sh
python3 -B tools/kernel/apply.py --source /path/to/linux --with-energy
python3 -B tools/kernel/apply.py --source /path/to/linux --with-energy --check
```

The supplied baseline directory must retain `inputs/stock.config`,
`inputs/manifest.json`, `inputs/meta-iwave/recipes-kernel/linux/linux-iwg/`,
`toolchain/manifest.json`, `baseline/stock-external-modules.json` and the baseline
kernel set paths recorded in that receipt. These are provenance inputs, not downloaded
implicitly by the builder. The native GCC 14.3 toolchain is supplied separately.
Vendor repository access and redistribution rights must be resolved independently;
this tool does not infer permission from having local files.

## Resource-bounded builds

`build.py` is native ARM64 only. An explicit cgroup v2 scope, CPU affinity, memory
limit, no swap and lowered priority are mandatory. Public resource arguments describe
the host reservation, not a board setting. Defaults are at most 150% CPU, 6 GiB memory,
2 make jobs and nice 10; `--allowed-cpus` is required. Choose available CPU IDs on your
host. This example uses CPUs 1 and 2 and must be adapted to the actual reservation:

```sh
baseline=/path/to/rel33
systemd-run --user --scope --collect --unit=iwave-kernel-driver \
  -p CPUQuota=150% -p AllowedCPUs=1,2 -p MemoryMax=6G -p MemorySwapMax=0 \
  -p RuntimeMaxSec=960 \
  taskset -c 1,2 nice -n 10 python3 -B tools/kernel/build.py \
  --allowed-cpus 1,2 --cpu-percent 150 --memory-gib 6 --jobs 2 --nice-min 10 \
  --source "$baseline/sources/linux" --baseline "$baseline" \
  --toolchain "$baseline/toolchain/root" --work "$baseline/work" \
  --output "$baseline/kernel-set" --receipt "$baseline/kernel-build.json" \
  --release 6.12.34-iwave-rel33-policy1 --phase driver
```

The focused phase compiles FlexCAN and the charger object. Reuse the same owned work
and receipt for `--phase full`, using a separate unit and `RuntimeMaxSec=11200`.
The individual full make has a 10800-second ceiling, with a separate bounded
`olddefconfig` stage when needed. Every spawned make has its own process group;
interruptions and timeouts stop descendants before returning. A failed build keeps
its owned work and receipt for diagnosis or a justified incremental retry.
Admission requires the measured 3 GiB native work/output allowance plus 500 MiB free
reserve. Active builds stop before consuming the final 500 MiB. Actual cgroup limits,
affinity, inputs, tool hashes, steps, exit codes and output hashes go into the receipt.

Kbuild uses the stock configuration after `olddefconfig`, exact common `KERNELRELEASE`
and source-prefix mapping. Do not independently rename a kernel or reuse modules from
a different candidate. New release labels do not prove byte-reproducible builds.

## Static qualification

After a completed full build, run `qualify.py` with the same explicit path and resource
arguments in the same kind of bounded scope, omitting `--phase` and `--release`.
Use an absent output directory. Allow a bounded outer scope of 2400 seconds; module
installation, IMU build, dependency checks and individual subprocesses have their own
deadlines. This step assembles Image, DTB, configuration and the complete module set:

1. Preserve the exact in-tree module names and all five stock external module bytes.
2. Build ST LSM6DSX core/I2C modules against the candidate headers and symbol versions,
   applying the existing-IRQ compatibility patch to a small module source directory.
3. Check the Image banner and embedded IKCONFIG against the compiled output, then
   independently compare parsed Image exports with `Module.symvers`.
4. Check every module's symbol CRCs, namespaces, GPL-only imports, vermagic, module
   layout, ARM64 relocations, dependency resolution and the DTB CAN/IMU identities.
5. Write manifests with exact artifact identities. Verify the two-module IMU package
   using the local manifest contract, without invoking an application installer.

The pure `image_exports.py` parser supports the pinned ARM64 Linux 6.12 PREL32 linker
layout only. It is not a general arbitrary-kernel parser. `module_abi.py` parses the
actual ELF modules and loader contracts; same-release text alone never proves ABI.
Existing `omam_` symbols, manifest schema identifiers and `updates/omam` directory
are stable compatibility formats. They do not contain an application or private config.

A passing static qualification is `PASS_BUILD_AND_STATIC_ABI`. Module loading,
interrupt behavior, physical energy measurements, wake and IMU samples remain
`NOT_RUN` until independently checked on the actual board. A retained qualified module
set is not automatically cleared for public binary redistribution: five external
modules still need complete corresponding-source/provenance resolution.

## Host regression checks

From the repository root:

```sh
timeout --kill-after=5s 120s sh tests/native/flexcan/run.sh
python3 -B -m unittest discover -s tests/native/flexcan -p 'test_*.py' -v
python3 -B -m unittest discover -s tests/python -p 'test_kernel_*.py' -v
python3 -B tests/native/flexcan/patch_test.py --source /path/to/pinned/linux
python3 -B tests/native/flexcan/run_rx_busy.py --kernel-source /path/to/pinned/linux
```

The C tests use UBSan and independent literal register/acceptance expectations. Python
cases cover ELF/loader errors, the Image layout, changed package bytes, extra payload,
wrong release, symlinks and explicit resource bounds. Patch-delivery fixtures contain
only the three needed vendor files; temporary source/binary data is deleted on exit.
No test opens a CAN socket, loads a kernel module or contacts a device. Use bounded
outer invocations for the Python suites when integrating on a shared machine.
