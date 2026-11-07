/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef _UAPI_LINUX_OMAM_FLEXCAN_H
#define _UAPI_LINUX_OMAM_FLEXCAN_H

#include <linux/types.h>

#define OMAM_FLEXCAN_FAMILY "omam_flexcan"
#define OMAM_FLEXCAN_VERSION 1
#define OMAM_FLEXCAN_MAX_ENTRIES 62
#define OMAM_FLEXCAN_DIGEST_SIZE 32

enum omam_flexcan_command {
	OMAM_FLEXCAN_GET_CAPS = 1,
	OMAM_FLEXCAN_GET_POLICY = 2,
	OMAM_FLEXCAN_CLAIM = 3,
	OMAM_FLEXCAN_VALIDATE = 4,
	OMAM_FLEXCAN_REPLACE = 5,
	OMAM_FLEXCAN_CLEAR = 6,
	OMAM_FLEXCAN_RELEASE = 7,
};
enum omam_flexcan_attribute {
	OMAM_FLEXCAN_A_REQUEST = 1,
	OMAM_FLEXCAN_A_RESPONSE = 2,
};
enum omam_flexcan_mode {
	OMAM_FLEXCAN_ACCEPT_ALL = 0,
	OMAM_FLEXCAN_ACCEPT_NONE = 1,
	OMAM_FLEXCAN_SELECTIVE = 2,
	/* Response applied_mode only: down, asleep, bus-off or unverified. */
	OMAM_FLEXCAN_UNAVAILABLE = 3,
};
enum omam_flexcan_format {
	OMAM_FLEXCAN_STANDARD = 0,
	OMAM_FLEXCAN_EXTENDED = 1,
};
enum omam_flexcan_rtr {
	OMAM_FLEXCAN_RTR_ANY = 0,
	OMAM_FLEXCAN_RTR_DATA = 1,
	OMAM_FLEXCAN_RTR_REMOTE = 2,
};
enum omam_flexcan_frame_kind {
	OMAM_FLEXCAN_FRAME_ANY = 0,
	/* Reserved requests: EDL is not part of the RXIMR acceptance mask. */
	OMAM_FLEXCAN_FRAME_CLASSIC = 1,
	OMAM_FLEXCAN_FRAME_FD = 2,
};
enum omam_flexcan_recovery {
	OMAM_FLEXCAN_RECOVERY_NONE = 0,
	OMAM_FLEXCAN_RECOVERY_PREVIOUS = 1,
	OMAM_FLEXCAN_RECOVERY_ACCEPT_ALL = 2,
	OMAM_FLEXCAN_RECOVERY_UNAVAILABLE = 3,
	OMAM_FLEXCAN_RECOVERY_CAPACITY = 4,
	OMAM_FLEXCAN_RECOVERY_OWNER_LOST = 5,
};
#define OMAM_FLEXCAN_F_SUPPORTED       (1U << 0)
#define OMAM_FLEXCAN_F_RUNNING         (1U << 1)
#define OMAM_FLEXCAN_F_OWNED           (1U << 2)
#define OMAM_FLEXCAN_F_READBACK        (1U << 3)
#define OMAM_FLEXCAN_F_REPLAY          (1U << 4)
#define OMAM_FLEXCAN_F_RX_BUSY_FAULT    (1U << 5)

/* Fixed little-endian wire structs. Every reserved/unused byte must be zero.
 * Entries are ORed, with replicas contiguous in ascending RX mailbox order.
 * No entry distinguishes Classic data from FD data on the same CAN identifier.
 */
struct omam_flexcan_entry {
	__le32 id;
	__le32 mask;
	__le16 replicas;
	__u8 format;
	__u8 rtr;
	__u8 frame_kind;
	__u8 reserved[3];
};

/* Exactly 1128 bytes. The payload digest is SHA-256 of this entire struct with
 * payload_digest zeroed, including command, generations and all unused entries.
 * GET requests use only abi, ifindex and command; all remaining bytes are zero.
 * Every mutation/VALIDATE requires a nonzero strictly increasing request_id.
 */
struct omam_flexcan_request {
	__le32 abi;
	__le32 ifindex;
	__le32 command;
	__le32 reserved;
	__le64 controller_instance;
	__le64 owner_epoch;
	__le64 expected_policy_generation;
	__le64 expected_capability_generation;
	__le64 profile_generation;
	__le64 request_id;
	__u8 payload_digest[32];
	__u8 profile_digest[32];
	__le32 mode;
	__le32 entry_count;
	struct omam_flexcan_entry entries[OMAM_FLEXCAN_MAX_ENTRIES];
};

/* Exactly 1168 bytes. result and last_error are signed errno values encoded in
 * 32 little-endian bits. mode/entries describe the committed desired policy;
 * applied_mode + READBACK describe the actual hardware state. READBACK means
 * verified when programmed, not that live RX data words remain unchanged.
 * A response with result < 0 is not successful policy application.
 */
struct omam_flexcan_response {
	__le32 abi;
	__le32 ifindex;
	__le32 result;
	__le32 flags;
	__le64 controller_instance;
	__le64 owner_epoch;
	__le64 policy_generation;
	__le64 capability_generation;
	__le64 receive_gap_sequence;
	__le64 profile_generation;
	__le64 last_request_id;
	__le64 rx_busy_failures;
	__le32 mode;
	__le32 applied_mode;
	__le32 rx_capacity;
	__le32 entry_count;
	__le32 ctrlmode;
	__le32 can_state;
	__le32 recovery;
	__le32 last_error;
	__u8 profile_digest[32];
	__u8 payload_digest[32];
	struct omam_flexcan_entry entries[OMAM_FLEXCAN_MAX_ENTRIES];
};
#endif
