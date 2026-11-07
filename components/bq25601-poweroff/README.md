# BQ25601 ship mode on graceful power-off

This optional GPLv2 kernel change targets the TI BQ25601 charger on the iWave
Telematics Connect Hub G62H i.MX91 REL3.3 profile. It is not a general suspend or
battery-management driver and does not establish support for other BQ256xx parts.

## Why this change exists

A halted CPU does not necessarily disconnect the backup battery. A low-current
power-off state needs an explicit charger BATFET operation, performed late enough
that storage and system shutdown can finish first. Earlier arming must not cut power.
The challenge is that charger register caching can hide the current silicon state,
while reboot and shutdown paths must remain different and a stale request must not
survive a later boot.

The patch adds RAM-only `ship_mode_on_poweroff` opt-in through the charger device's
sysfs attribute. Arming validates both the configured BQ25601 model and the physical
part information through a bypassed regmap read. Arming/cancelling changes no BATFET
register. Only graceful system power-off consumes the request; reboot does not.
The shutdown hook sets the BATFET delay and disable bits, preserving unrelated bits.
Relevant charger control bits are marked volatile so live register values are used.
The feature starts disarmed, reports errors and does not manufacture a success result.

An application must establish a real wake path for its power mode before opting in.
This repository supplies the mechanism, not an unattended energy policy or a promise
that ignition, modem or CAN can wake a board whose battery path was disconnected.
Physical current, rail sequencing and wake measurements remain required.

## Exact inputs and checks

`source.json` pins the upstream revision, REL3.3 vendor-patched source path, pre/post
SHA-256 and patch identity. Base revision is
`02cf250c2e6bd375c11dae324ea145a39015d41b`, with all 15 supplied REL3.3 patches.
Apply from this repository root to an owned, prepared Linux tree:

```sh
python3 -B tools/kernel/apply.py --source /path/to/linux --with-energy
python3 -B tools/kernel/apply.py --source /path/to/linux --with-energy --check
```

Unknown sources, modified patches and partial application are refused. Hashes of the
complete resulting driver are checked; patches use zero fuzz and zero offsets.
These commands do not build, load or install software and do not contact a device.

`native_test.py` extracts the production hook block after applying the exact patch
and compiles it with `native_harness.c`, using an injected regmap/sysfs boundary.
It covers model/part identity, register-read/write failures, arm/cancel, reboot versus
power-off, request consumption and preservation of unrelated register bits.
Use a pre-patch driver file with the exact `preSha256` from `source.json`:

```sh
python3 -B components/bq25601-poweroff/native_test.py \
  --source /path/to/pre-patch/bq256xx_charger.c --report /path/to/native-report.json
```

This host test does not establish kernel integration, physical BATFET timing, current
consumption, brownout safety or wake behavior. The kernel build and static loader
qualification are separate checks; hardware acceptance is a separate explicit step.
