/* SPDX-License-Identifier: GPL-2.0 */
#ifndef OMAM_FLEXCAN_POLICY_H
#define OMAM_FLEXCAN_POLICY_H

#include <linux/omam_flexcan.h>
#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/string.h>
#include <linux/byteorder/generic.h>
#else
#include <errno.h>
#include <string.h>
#include <endian.h>
#define le16_to_cpu le16toh
#define le32_to_cpu le32toh
#define le64_to_cpu le64toh
#define cpu_to_le32 htole32
#endif

#define OMAM_FLEXCAN_RX_POLL_DELAY_US 1
#define OMAM_FLEXCAN_RX_POLL_TIMEOUT_US 250

#define OMAM_RX_EMPTY (4U << 24)
#define OMAM_RX_IDE   (1U << 21)
#define OMAM_RX_RTR   (1U << 20)
#define OMAM_MASK_IDE (1U << 30)
#define OMAM_MASK_RTR (1U << 31)

struct omam_flexcan_program {
	__u32 id[OMAM_FLEXCAN_MAX_ENTRIES];
	__u32 mask[OMAM_FLEXCAN_MAX_ENTRIES];
	__u32 control[OMAM_FLEXCAN_MAX_ENTRIES];
	__u32 count;
};

/* Compile a complete mailbox program without allocation, MMIO or state changes. */
static inline int omam_flexcan_compile(const struct omam_flexcan_request *req,
				      unsigned int capacity,
				      struct omam_flexcan_program *out)
{
	unsigned int mode = le32_to_cpu(req->mode);
	unsigned int count = le32_to_cpu(req->entry_count);
	unsigned int i, j, slot = 0;

	if (!capacity || capacity > OMAM_FLEXCAN_MAX_ENTRIES)
		return -EOPNOTSUPP;
	if (req->reserved || mode > OMAM_FLEXCAN_SELECTIVE ||
	    count > OMAM_FLEXCAN_MAX_ENTRIES)
		return -EINVAL;
	if ((mode == OMAM_FLEXCAN_SELECTIVE) != (count != 0))
		return -EINVAL;
	memset(out, 0, sizeof(*out));
	for (i = 0; i < OMAM_FLEXCAN_MAX_ENTRIES; i++) {
		const struct omam_flexcan_entry *e = &req->entries[i];
		__u32 id = le32_to_cpu(e->id), mask = le32_to_cpu(e->mask);
		__u32 limit, ctrl, regmask;
		unsigned int replicas = le16_to_cpu(e->replicas);

		if (i >= count) {
			static const struct omam_flexcan_entry zero;

			if (memcmp(e, &zero, sizeof(*e)))
				return -EINVAL;
			continue;
		}
		if (e->format > OMAM_FLEXCAN_EXTENDED ||
		    e->rtr > OMAM_FLEXCAN_RTR_REMOTE ||
		    e->reserved[0] || e->reserved[1] || e->reserved[2] || !replicas)
			return -EINVAL;
		if (e->frame_kind != OMAM_FLEXCAN_FRAME_ANY)
			return -EOPNOTSUPP;
		limit = e->format == OMAM_FLEXCAN_EXTENDED ? 0x1fffffff : 0x7ff;
		if ((id | mask) & ~limit || (id & ~mask))
			return -EINVAL;
		if (replicas > capacity - slot)
			return -ENOSPC;
		ctrl = OMAM_RX_EMPTY;
		regmask = OMAM_MASK_IDE;
		if (e->format == OMAM_FLEXCAN_EXTENDED)
			ctrl |= OMAM_RX_IDE;
		else {
			id <<= 18;
			mask <<= 18;
		}
		if (e->rtr != OMAM_FLEXCAN_RTR_ANY) {
			regmask |= OMAM_MASK_RTR;
			if (e->rtr == OMAM_FLEXCAN_RTR_REMOTE)
				ctrl |= OMAM_RX_RTR;
		}
		for (j = 0; j < replicas; j++, slot++) {
			out->id[slot] = id;
			out->mask[slot] = regmask | mask;
			out->control[slot] = ctrl;
		}
	}
	if (mode == OMAM_FLEXCAN_ACCEPT_ALL) {
		for (slot = 0; slot < capacity; slot++)
			out->control[slot] = OMAM_RX_EMPTY;
	}
	out->count = slot;
	return 0;
}

struct omam_flexcan_identity {
	__u64 instance;
	__u64 owner_epoch;
	__u64 owner_cookie;
	__u64 policy_generation;
	__u64 capability_generation;
	__u64 replay_cookie;
	__u64 replay_id;
};

/* Return 1 for the exact last replay, 0 for an admissible new command. The full
 * previous bytes, not a caller's declared coverage/digest, identify a replay.
 */
static inline int omam_flexcan_authorize(const struct omam_flexcan_identity *s,
		const struct omam_flexcan_request *req, __u64 cookie,
		const struct omam_flexcan_request *previous)
{
	__u64 id = le64_to_cpu(req->request_id);
	unsigned int command = le32_to_cpu(req->command);

	if (!id || le64_to_cpu(req->controller_instance) != s->instance)
		return -ESTALE;
	if (cookie == s->replay_cookie && id <= s->replay_id) {
		if (id != s->replay_id)
			return -ESTALE;
		return memcmp(req, previous, sizeof(*req)) ? -EALREADY : 1;
	}
	if (le64_to_cpu(req->expected_policy_generation) != s->policy_generation ||
	    le64_to_cpu(req->expected_capability_generation) != s->capability_generation)
		return -ESTALE;
	if (command == OMAM_FLEXCAN_CLAIM)
		return s->owner_epoch ? -EBUSY : (req->owner_epoch ? -ESTALE : 0);
	if (!s->owner_epoch || le64_to_cpu(req->owner_epoch) != s->owner_epoch ||
	    cookie != s->owner_cookie)
		return -EPERM;
	return 0;
}

static inline int omam_flexcan_cleanup_matches(const struct omam_flexcan_identity *s,
					     __u64 departed_epoch)
{
	return departed_epoch && s->owner_epoch == departed_epoch;
}

struct omam_flexcan_io {
	int (*freeze)(void *context);
	int (*program)(void *context, const struct omam_flexcan_request *policy);
	int (*unfreeze)(void *context);
};

static inline int omam_flexcan_attempt(const struct omam_flexcan_io *io, void *context,
				     const struct omam_flexcan_request *policy)
{
	int err = io->freeze(context);

	if (!err)
		err = io->program(context, policy);
	if (!err)
		err = io->unfreeze(context);
	return err;
}

/* One candidate attempt, one complete rollback, one receive-all recovery. The
 * callback interface is also used by native tests with independently scripted
 * hardware faults. A failure is returned even when recovery succeeds.
 */
static inline int omam_flexcan_transition(const struct omam_flexcan_io *io, void *context,
		const struct omam_flexcan_request *candidate,
		const struct omam_flexcan_request *previous, unsigned int *recovery)
{
	static const struct omam_flexcan_request all;
	int err = omam_flexcan_attempt(io, context, candidate);

	*recovery = OMAM_FLEXCAN_RECOVERY_NONE;
	if (!err)
		return 0;
	*recovery = OMAM_FLEXCAN_RECOVERY_PREVIOUS;
	if (!omam_flexcan_attempt(io, context, previous))
		return err;
	*recovery = OMAM_FLEXCAN_RECOVERY_ACCEPT_ALL;
	if (!omam_flexcan_attempt(io, context, &all))
		return err;
	*recovery = OMAM_FLEXCAN_RECOVERY_UNAVAILABLE;
	return err;
}
#endif
