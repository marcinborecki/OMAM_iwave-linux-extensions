# Architecture and verification contracts

The unit of compatibility is a hardware profile. A profile fixes the vendor input
identity, source revision and patch sequence, Device Tree, kernel configuration,
module set and SDK. The component directories own mechanisms; the profile decides
where those mechanisms are valid. A controller feature flag is not a board-level
qualification, and a working source build is not a measurement of wake current.

## Source and binary identity

The first profile starts with an iWave-pinned Linux revision and fifteen ordered
vendor patches. The FlexCAN overlay manifest binds the base and result hashes of
all affected files. The charger manifest binds its original source, patch and
post-image. The IMU patch binds the external module's source and the expected IRQ
assumptions. Check/apply commands refuse a different source; they do not accept a
close-looking version or apply a patch with arbitrary offsets.

The compiled kernel set records Image, DTB, configuration and symbol/map outputs.
It inventories every module and depmod index, including the five exact stock
external modules and the upstream IMU pair. Qualification compares symbol CRCs,
module layout, relocation support and loader version semantics. Counting files or
comparing only vermagic would miss important incompatible states.

The Image export parser is its own public module. Its PREL32 chain assembly keeps
chains by stride and offset rather than extending only whichever chain was seen
last. A synthetic interleaving regression detects the older behavior that selected
a shorter apparently valid sequence. The real qualified Image was also compared
with the independent Module.symvers export table. That combination checks both a
specific failure mode and the actual candidate without executing vendor binaries.

## Neutralisation as an explicit transformation

The stock rootfs is an immutable input. A declared overlay plan lists each deletion
and replacement. Composition streams the input into a new archive and preserves
all untouched entries. This makes changed ownership, unexpected hardlinks, missing
parents and unplanned overwrites observable rather than relying on a shell copy.

Neutralisation is profile-specific. It removes stored connections, SIM/PPP state,
old machine/host identity and demonstration home data; it locks password accounts.
It does not search the whole source tree with a generic credential heuristic or
silently modify the original acquired input. An independent reader inspects the
actual output, not just the list of intended deletions.

Removing configuration also changes service dependencies. The stock graphical
target needed a home directory that no longer existed, and the stock modem setup
expected a PPP peer file that was deliberately absent. The neutral profile therefore
selects headless multi-user boot and masks those consumers until an installation
profile provides a replacement. The factory battery monitor's enablement,
executable and library are verified as a connected path. Physical effectiveness
remains an instrumented device test.

## Private additive profiles

A private application profile is a separate producer. It validates the public
base before any publication or privileged installation work, adds its declared
application/configuration overlay and verifies retained content against that base.
The overlay composer does not add a second SDK. Kernel/modules may be temporarily
made visible to an offline installer for validation, but they are not changed in
the final base content.

Application signing, per-device certificate issuance, SIM/APN settings and server
configuration belong to the downstream application. Their absence from the public
base is deliberate and checked. Public source/ABI identifiers that retain an
original project prefix do not constitute application provisioning.

## Evidence levels

| Evidence | What it establishes | What it leaves open |
|---|---|---|
| Source/hash check | Exact reviewed inputs and declared changes | Compilation and runtime behavior |
| Native test | The exercised algorithm/refusal behavior with independent expectations | Real CAN controller, sensor or battery behavior |
| Kernel build | The selected source/config/toolchain can produce outputs | Correct module loading and physical effects |
| Static ABI qualification | Declared loader contracts and exact module/index identities | Actual boot, traffic, interrupts and wake |
| Archive acceptance | Actual kernel/SDK/configuration content and software boot connections | Physical flash/boot and measured power behavior |
| Hardware measurement | The observed device, installation and workload | Untested boards, indefinite operation or different conditions |

A release report should state each level separately. Skipped tests, an empty suite,
an absent compiler or an unavailable device are not passing evidence. A repaired
input invalidates only the checks it actually changes. Preserve an earlier failed
result as failed when later scoped replacements are accepted.
