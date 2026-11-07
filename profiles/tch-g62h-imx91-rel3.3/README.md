# Telematics Connect Hub G62H, i.MX91, REL3.3

This profile targets the iWave Telematics Connect Hub development hardware using
NXP i.MX91 and `imx91-iwg62h-prgtt.dtb`. It does not establish compatibility with
all G62H production options or other iWave products. REL3.3 identifies this board's
vendor BSP release, not a common firmware version for the manufacturer's catalogue.

This is the operating-system profile beneath a downstream application. Linux
manages CPU, memory, storage and drivers; base services retain their configured
platform roles, including factory battery protection. A separate application
chooses vehicle decoding, capture/sample policy, operational energy decisions and
its service connection through the provided interfaces.

The current qualified set uses Linux `6.12.34-omam-rel33-policy1` and .NET SDK
`11.0.100-rc.3.26522.148` for Linux ARM64. The SDK and runtime are deliberately pinned
to the existing RC3 payload. Official .NET 11 channel metadata checked on 2026-11-07 identifies RC3 as
the latest published RC at that check; sdk-release-verification.json records it.
`dotnet-sdk.lock.json` records the Microsoft download URL and SHA512;
`image-policy.json` binds the actual SDK tree and qualified kernel manifest.
The historical `omam` kernel release suffix and module/UAPI names identify stable
interfaces and existing provenance. They do not install the private application.

## What the base contains

The base combines the immutable stock rootfs with the qualified kernel's Image,
DTB, 807 native modules, five preserved vendor external modules and two IMU modules.
The kernel module indexes are rebuilt and qualified with that release. It includes
the full pinned .NET SDK under `/opt/dotnet`, a `/usr/local/bin/dotnet` symlink and
`/etc/profile.d/dotnet.sh`. No private application publication is an input.

The FlexCAN changes provide explicit controller receive-filter policy and status
through the preserved `omam_flexcan` interface. They address acceptance filtering
and controller readback requirements on the qualified FlexCAN IP. They do not make
arbitrary CAN traffic safe or qualify another controller implementation.

The BQ25601 charger change makes the ship-on-poweroff transition explicit. The
power failure and wake behavior still depends on the board's charger, wiring,
firmware and the user's power policy. The base preserves the factory battery
monitor and vendor runtime library. It does not install automatic private
application energy management.

The ST LSM6DSX change handles an existing IRQ mapping when switching from the
vendor sensor driver. It is specific to the reviewed interrupt topology and is
not a claim that every ST IMU or every Device Tree is supported. Installing the
qualified modules does not prove an actual driver handoff or sensor readback.

## Mechanisms and application activation

| Area | Platform capability in this profile | Application role |
|---|---|---|
| CAN | Patched built-in FlexCAN accepts a complete, observable controller policy. | Choose the identifiers/masks that matter on a busy bus and retain exact decoding/filtering. |
| Motion | Built-in vendor IMU driver plus an externally built upstream pair with the IRQ-handoff fix. | Select the owner, channels and a supported rate such as104Hz, then interpret XYZ samples. |
| Power | Linux idle/suspend infrastructure, factory protection and controlled BQ25601 shutdown intent. | Coordinate work/flush, choose a supported state, prepare wake or external-power recovery, and observe the result. |

This profile binds those mechanisms to the actual kernel, module and SDK identities.
The runtime application uses their interfaces to implement its operational policy.

## Deliberate neutralisation

The build reads only pinned vendor files and a staged SDK. It never takes an
installed device, owner's rootfs, SIM profile or application configuration as input.
It removes vendor root/home demonstration payloads, configured PPP peers and modem
connection scripts, saved NetworkManager/connman/WPA connections, Bluetooth pairing
state, host SSH keys, authorized keys, machine identity and random-seed state.
Vendor LTP test payload is omitted from the production base.

The profile explicitly boots `multi-user.target` in headless mode. It removes the
enablement links and masks `weston.service`, `weston.socket` and `assm_conf.service`.
Weston otherwise requires the removed `/home/weston` working directory. The vendor
`assm_conf` program opens the removed PPP peer file; leaving it enabled would
retain a broken consumer of unprovisioned modem configuration. A separate private
profile can deliberately replace the masks once it supplies its required files.

The factory `battery_monitor.service` remains enabled with its original monitor
binary and `libTelematics.so`. BLE initialisation, GNSS socket activation and SSH
host-key generation retain the stock entrypoints. The independent archive check
requires the headless target, all three masks and enabled factory protection. It
rejects an earlier archive that lacks these connections even if its old manifest
records a passing privacy check. CAN utilities,
factory protection and unrelated vendor runtime bytes remain unchanged.

All password fields in the stock shadow and gshadow databases are locked. The
hostname becomes `iwave-tch`; `/etc/machine-id` is empty for systemd first boot.
There is no default owner password or preconfigured login key. Prepare access in
your separate private profile before flashing. Merely flashing this base is not
an instruction to expect SSH access or cellular connectivity.

The acceptance tool independently checks the final archive for the explicit
provisioning paths and relevant `/etc` text, including active APN/PIN
declarations. This is a scoped check over an exact vendor input, not a general secret
scanner. An installed system subsequently generates its own identity and keys.

## Local build

Install Python 3 and zstd. Stage the exact stock BSP, kernel set, kernel qualification
receipt and locked SDK. The build performs no downloads, device access or flash.
The receipt can be kept outside this repository and is passed explicitly. From
the project root, run:

```sh
python3 tools/image/build_base.py \
  --profile tch-g62h-imx91-rel3.3 \
  --vendor-root .local/vendor/tch-g62h-imx91-rel3.3 \
  --vendor-manifest .local/vendor-manifest.json \
  --kernel-root .local/kernel-set \
  --kernel-receipt /absolute/path/to/kernel-build-receipt.json \
  --sdk .local/sdk/dotnet-11.0.100-rc.3.26522.148-linux-arm64 \
  --scratch-root /tmp \
  --output /tmp/iwave-neutral-base \
  --dry-run
```

Remove `--dry-run` to compose a new output directory. The dry run validates the
actual file pins and storage reserve but does not prove final archive content.
The builder conservatively reserves space for the compressed candidate, boot
files, staged kernel overlay and at least 500 MiB free. `--scratch-root` controls
the small staging overlay; the candidate itself is written beside `--output`.
Use an output filesystem with sufficient free space. No whole-rootfs extraction
or second full repository is needed. Failed or interrupted builds remove their
owned incomplete output and temporary overlay.

The output has `Linux/rootfs.tar.zst`, matching `Linux/Image` and DTB, the stock
recovery Firmware files, stock bootloader and UUU script, plus `base-manifest.json`.
The manifest contains file hashes, source identities, SDK identity, verification
results and explicit physical/release gaps. The flasher writes an entire device:
review the manufacturer's instructions and your own provisioning before using it.
This builder never invokes it.

## Application layering

Keep your application, version, deployment configuration, SIM/APN, credentials,
SSH access and device identity in a different private project. Its builder must
first call `tools/image/build_base.py`'s `validate_base(base_directory)` function.
That function checks every declared boot/rootfs file, the profile's exact kernel
and SDK pins, and the actual archive's neutral content, SDK bytes and module set.
An altered or unrelated base fails before private output is prepared.

The private consumer can then use `compose_rootfs.compose_overlay(rootfs,
rootfs_sha256, output, overlay_dir, overlay_plan, source_date_epoch=0,
preserve_overlay_ownership=False)`. The overlay plan is schema version 1 with
explicit `delete` and `replace` path arrays. Paths that only add new files need
no replacement declaration; overwrites require one. SDK paths are protected.
This operation preserves the existing SDK instead of injecting it again.
`verify_composed_rootfs.verify_overlay(base, candidate, overlay_dir, overlay_plan)`
checks all retained entries byte for byte and compares the applied overlay.
Changing the kernel or SDK requires a separately qualified base profile.

## Acceptance and redistribution limits

Host composition and archive checks do not prove booting, CAN traffic, energy
transitions, wakeup or IMU readings on physical hardware. Those remain `NOT_RUN`
unless separately documented for this exact artifact. Preserve rollback access
when testing hardware.

The locally usable base is marked `redistribution_status: BLOCKED`. The proprietary
vendor library and firmware require confirmed redistribution terms. Five preserved
external modules require complete matching-source assessment. A complete
corresponding-source release must accompany distributed kernel/module binaries as
applicable. A small public patch repository and pinned vendor URL alone do not
close those source gaps. Do not publish the binary base merely because local
privacy and hash checks pass.
