# iWave REL3.3 FlexCAN controller policy

This package implements the `omam_flexcan` Generic Netlink v1 ABI in
`include/uapi/linux/omam_flexcan.h`. It is qualified
in source for the i.MX91's `fsl,imx93-flexcan` controller, RX mailbox mode with EACEN/RRS,
and `CONFIG_CRYPTO_LIB_SHA256=y`. It does not create a CAN data receiver or transmit
CAN frames. Other FlexCAN IPs report unsupported and retain stock filtering.

The base is `linux-imx9-iwave` SRCREV `02cf250c2e6bd375c11dae324ea145a39015d41b`
plus all 15 supplied REL3.3 patches. None of those patches changes the three files
modified by `0001-flexcan-controller-policy.patch`. `manifest.json` pins each base
and result file. The new C/headers in this directory are the source of truth for the
four overlays. The patch contains only the three existing driver files. The source
acquisition receipt remains responsible for all other BSP inputs.

From the repository root, with an explicitly prepared source directory:

```sh
python3 tools/kernel/apply.py --source /path/to/prepared/linux
python3 tools/kernel/apply.py --source /path/to/prepared/linux --check
```

Application validates every base and new overlay before writing, performs a patch
dry run, and verifies all seven results. An already applied exact result is a no-op.
An unknown or partly applied input is refused. Neither command compiles, installs,
loads modules, changes services or contacts a device. The integrated image build owns
kernel/DTB/module ABI verification and device qualification.

## Original problem, implementation and limits

SocketCAN socket filters run after controller reception. They cannot prevent irrelevant
bus traffic from consuming receive mailboxes, interrupts and driver work. Under two-bus
bursts the important challenge is to reduce controller admission without starving a bus,
silently dropping a requested rule or claiming that a policy was programmed when hardware
rejected it. This extension adds an explicitly owned cold control path for hardware RX
mailbox policy and readback. It leaves the stock receive-all behavior when unclaimed.

The difficult parts are finite mailbox capacity, overlapping ID masks, receive rearming,
Classic versus FD layouts, live transitions, owner loss and rollback after partial MMIO
failure. The compiler produces an entire bounded mailbox plan before touching hardware;
the transaction reports actual mode, generations and recovery state. The legacy `omam_`
prefix identifies the stable public wire ABI and source symbols. It carries no application
binary, deployment profile, SIM settings or account identity.

This is not a generic CAN performance claim: IRQ reduction, bus fairness, physical mask
behavior and wake behavior need independently transmitted traffic on the target board.
No new support is claimed for another iWave model or controller revision.

## Wire and ownership

Resolve the family through `GENL_ID_CTRL`; do not pin its runtime numeric ID. Send
exactly one binary attribute, type 1, containing a 1128-byte request. The reply has
one type-2 binary attribute with a 1168-byte response. Header version is 1 and all
struct integers are little-endian. The command also appears inside the request.
All reserved fields and unused entries must be zero. The entry layout is 16 bytes:
ID, mask, replica count, standard/extended format, RTR policy, frame kind, reserved.

GET_CAPS and GET_POLICY contain only ABI version, ifindex and command. Mutating
operations and VALIDATE carry the returned controller instance, expected generations,
owner epoch, a strictly increasing nonzero request ID, profile identity and SHA-256
of the complete request with the payload-digest field zeroed. CLAIM uses epoch zero;
CLEAR/RELEASE/CLAIM require receive-all, zero entries and zero profile identity.
VALIDATE compiles a complete plan without MMIO or policy-generation changes.

The owner is a long-lived Generic Netlink socket, authenticated with the sender's
network-namespace CAP_NET_ADMIN. The kernel retains that actual socket and uses its
kernel cookie for replay authorization. It never ties a claim to an RX socket.
Ifindex or netlink portid reuse cannot acquire an old lease. Closing the owner socket
schedules bounded cleanup; cleanup checks the departed epoch before changing state.
Namespace moves clear ownership and renew controller identity. Physical removal
cancels owned work before private memory is freed.

One full request is retained for idempotent replay. Identical last requests return
current status with REPLAY; changed bytes using that ID and older IDs are refused.
After a reset, loss or capacity fallback, a replay may return a different actual
policy than the original response. Callers must compare current generations, digest,
mode and READBACK instead of interpreting a repeated result code as readiness.

## Controller behavior

A new controller starts with receive-all. SELECTIVE assigns complete OR rules and
explicit replica counts to ascending RX slots, leaving unused slots inactive. MB0
and the final TX mailbox are reserved. Capacity is 62 in Classic mode or 12 with
64-byte FD mailboxes; replicas consume that same pool. No hidden buffer reserve,
rule truncation or compiler coverage claim exists in the driver. Overcapacity
returns ENOSPC. The caller chooses measured replica allocation or an explicit
receive-all fallback while retaining exact socket/application filters.

Masks preserve IDE and optionally RTR. EDL is not an RXIMR acceptance bit, so
Classic-only or FD-only entry requests return EOPNOTSUPP; ANY admits both data-frame
kinds permitted by the configured controller. FD mode can still receive Classic
RTR frames. A received ID only replaces ignored ID bits or matches the configured
ones, so no new per-frame rearm writes are added. Physical repeated-mask behavior
still requires device qualification.

Live application stops TX admission, synchronizes IRQs and refuses a pending TX.
It quiesces NAPI, drains queued old-generation frames, freezes, programs every RX
slot and verifies its mask/ID/control readback, then unfreezes. Failures attempt the
previous complete policy and then receive-all. If neither succeeds, the controller
is made unavailable and no successful filtering/readiness is reported. A receive-gap
sequence exposes transitions; there is no fabricated hardware rejection counter.

Down-state requests stage the desired policy. They return `applied_mode=UNAVAILABLE`
and no READBACK. Open/down-up, bus-off restart and resume reapply before readiness.
A Classic-to-FD policy which no longer fits becomes explicit receive-all with a new
policy generation and CAPACITY recovery status. Owner loss restores receive-all or
marks reception unavailable when hardware cannot establish it. RX_BUSY polling is
bounded; expiry suppresses RX IRQ admission, exposes the fault and requires reapply
or reset. TX and error handling are separate.

`mode` and entries describe the committed desired policy. Actual readiness requires
`result == 0`, `applied_mode == mode`, READBACK, correct policy/profile identity and a
usable link state. READBACK refers to verification at programming time; live RX
mailbox contents naturally change. The application must coordinate both controllers;
this ABI provides no cross-controller atomic commit and no wake-on-selected-ID claim.

## Verification boundary

`tests/native/flexcan/run.sh` compiles and executes the actual portable compiler,
authorization checks and bounded transaction/recovery logic with UBSan. Independent
literal register vectors, exhaustive standard IDs, extended/J1939 groups, format/RTR,
capacity, replay and scripted hardware faults protect the cold control path.
`patch_test.py --source /path/to/prepared/linux` checks application, idempotence and
refusal using a small disposable three-file vendor fixture, not a repository copy.

These tests do not establish kernel compilation, hardware MMIO behavior, pending-TX
races, real socket-close notification, PM/wake, error IRQ behavior or receive throughput.
The retained candidate was compiled and statically qualified. Physical observations
remain NOT RUN and are never inferred from these native checks.
That later validation includes two-bus bursts, repeated masked reception, accept-none
ACK/TX semantics and controller rejection against independently transmitted counts.
No device operation is performed by this package's preparation or native tests.
