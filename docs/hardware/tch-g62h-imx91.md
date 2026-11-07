# Telematics Connect Hub G62H: hardware boundary

The current profile is for the iWave TCH family using NXP i.MX91. Its normal-boot
Device Tree is imx91-iwg62h-prgtt.dtb. The vendor delivery also contains recovery
firmware, a flash boot container and a UUU script; those serve different stages of
installation. A recovery Device Tree must not be substituted for the normal-boot
Device Tree simply because both names contain i.MX91.

## CAN controller

The Linux transport is SocketCAN. Controller-policy control is a separate Generic
Netlink path implemented inside FlexCAN, while the application still owns its CAN
data socket. The qualified controller advertises the IP compatibility/features
checked by the extension. i.MX91 uses a controller compatible string containing
i.MX93; this is an IP description, not proof that an arbitrary i.MX93 carrier is
supported by the image.

The hardware layout constrains policy capacity. The extension accounts for the
mailbox banks, reserved transmit mailbox and the applicable receive erratum.
Validation must reject a policy that cannot be represented by that controller.
Increasing a userspace queue cannot add controller filter capacity. Host policy
tests and static register logic are useful, but actual filter readback and traffic
must be measured on the board and transceiver configuration being qualified.

## Charger and power ownership

The charger patch targets the BQ25601 behavior reached through the existing
bq256xx driver. A successful register transaction is different from the system's
supply rail actually reaching its intended state. The RAM-only power-off request
prevents a transient maintenance decision from persisting as an unconditional boot
policy. Supply reconnection, external power, battery state and the installed wake
path must be considered together before enabling sleep/shipment behavior.

The neutral image retains the vendor battery monitor's default software path.
An application can later introduce its own bounded management/ownership protocol,
but that is not supplied by this repository's bare kernel patch. An application
must know how to return control, report actual state and preserve a proven wake
source. No source-code change alone proves battery life or current consumption.

## Accelerometer and gyroscope

The affected board carries an ST IMU managed at boot by the built-in vendor driver.
The upstream st_lsm6dsx pair is the alternate buffered-data owner. These drivers
must not own the same sensor simultaneously. Unbinding the first driver does not
necessarily remove an interrupt-domain mapping, which explains why the second
probe can encounter conflicting trigger information.

The narrow workaround looks up an existing mapping under known node/parent and
specifier assumptions. It is not a general instruction to retype an interrupt or
force a module into the kernel. The pair must match the selected kernel's symbol
versions and module layout. Per-installation takeover policy, process privileges
and sensor sampling configuration belong to the application/integration stage.

## Network, time and first boot

The neutral base contains no cellular account, SIM PIN, selected APN or remembered
Wi-Fi connection. Its modem setup unit is masked until a downstream profile can
provide a coherent configuration. Network reachability therefore remains an
installation responsibility. GNSS or network time cannot be assumed before those
services have their actual device/configuration inputs.

Machine identity and SSH host keys are generated per installation through the
normal systemd/OpenSSH path. Stock password accounts are locked and no shared
image credential is supplied. Provide access in a separate controlled profile;
do not put a real password, authorized-key set or private certificate in a public
example. An immutable base is not a preconfigured remotely reachable device.

## Porting to another iWave board

Record its exact model/revision and component inventory first: SoC, controller IP,
transceivers, charger, IMU, interrupt wiring, storage, boot chain, modem and expected
wake sources. Compare those contracts with each component's assumptions. Use a new
profile and new Device Tree/configuration and retain source/patch identities.

A different product can share Linux source and common components without sharing
an image. More CAN ports, another modem or a dual-core CPU changes neither the GPL
source obligations nor the need for board-specific boot and peripheral validation.
Do not advertise compatibility based only on the iWave brand, a kernel version or
a superficially similar connector.
