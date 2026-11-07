/* SPDX-License-Identifier: GPL-2.0 */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include "flexcan-policy.h"

static unsigned int cases;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x); exit(1); } } while (0)
#define PASS(name) do { cases++; printf("ok %u - %s\n", cases, name); } while (0)

static struct omam_flexcan_request policy(unsigned int mode)
{
	struct omam_flexcan_request r = {0};
	r.mode = htole32(mode);
	return r;
}
static void entry(struct omam_flexcan_request *r, unsigned int n, unsigned int id,
		  unsigned int mask, unsigned int format, unsigned int rtr, unsigned int copies)
{
	r->entries[n] = (struct omam_flexcan_entry) {
		.id = htole32(id), .mask = htole32(mask), .replicas = htole16(copies),
		.format = format, .rtr = rtr,
	};
	r->entry_count = htole32(n + 1);
}

/* Independent mailbox acceptance oracle: RXIMR compares ID, IDE and RTR.
 * The literal layout vectors below guard this oracle's bit placement too.
 */
static int accepted(const struct omam_flexcan_program *p, unsigned int capacity,
		    unsigned int id, int extended, int remote)
{
	unsigned int frame = extended ? id : id * 262144U;
	unsigned int i;
	frame |= extended ? 0x40000000U : 0;
	frame |= remote ? 0x80000000U : 0;
	for (i = 0; i < capacity; i++) {
		unsigned int reference = p->id[i];
		if (!p->control[i]) continue;
		if (p->control[i] & 0x00200000U) reference |= 0x40000000U;
		if (p->control[i] & 0x00100000U) reference |= 0x80000000U;
		if ((frame & p->mask[i]) == (reference & p->mask[i])) return 1;
	}
	return 0;
}

struct hardware {
	int fail[10];
	unsigned int calls;
	unsigned int mode;
	int running;
};
static int next(struct hardware *h)
{
	CHECK(h->calls < 9);
	return h->fail[++h->calls];
}
static int freeze(void *context)
{
	struct hardware *h = context;
	int err = next(h);
	if (!err) h->running = 0;
	return err;
}
static int program(void *context, const struct omam_flexcan_request *r)
{
	struct hardware *h = context;
	int err = next(h);
	CHECK(!h->running);
	if (!err) h->mode = le32toh(r->mode);
	return err;
}
static int unfreeze(void *context)
{
	struct hardware *h = context;
	int err = next(h);
	if (!err) h->running = 1;
	return err;
}
static const struct omam_flexcan_io io = { freeze, program, unfreeze };

int main(void)
{
	struct omam_flexcan_request r, previous;
	struct omam_flexcan_program p;
	unsigned int id, format, remote, recovery;
	struct omam_flexcan_identity state = {
		.instance = 91, .owner_epoch = 10, .owner_cookie = 30,
		.policy_generation = 5, .capability_generation = 7,
	};
	struct hardware h;
	puts("TAP version 13");
	CHECK(sizeof(struct omam_flexcan_entry) == 16);
	CHECK(sizeof(struct omam_flexcan_request) == 1128);
	CHECK(sizeof(struct omam_flexcan_response) == 1168);
	CHECK(offsetof(struct omam_flexcan_request, payload_digest) == 64);
	CHECK(offsetof(struct omam_flexcan_request, entries) == 136);
	CHECK(offsetof(struct omam_flexcan_response, entries) == 176);
	PASS("fixed v1 wire lengths and offsets");

	r = policy(OMAM_FLEXCAN_SELECTIVE);
	entry(&r, 0, 0x123, 0x7ff, 0, 1, 1);
	CHECK(!omam_flexcan_compile(&r, 62, &p));
	CHECK(p.id[0] == 0x048c0000 && p.mask[0] == 0xdffc0000 && p.control[0] == 0x04000000);
	for (id = 0; id < 2048; id++) for (format = 0; format < 2; format++) for (remote = 0; remote < 2; remote++)
		CHECK(accepted(&p, 62, id, format, remote) == (id == 0x123 && !format && !remote));
	PASS("all 2048 standard IDs with IDE/RTR separation against exact rule");

	entry(&r, 0, 0x120, 0x7f8, 0, 0, 4);
	CHECK(!omam_flexcan_compile(&r, 12, &p));
	CHECK(p.count == 4);
	for (id = 0; id < 2048; id++) for (remote = 0; remote < 2; remote++)
		CHECK(accepted(&p, 12, id, 0, remote) == (id >= 0x120 && id <= 0x127));
	for (id = 0x120; id <= 0x127; id++) {
		p.id[0] = id * 262144U; /* Hardware overwrites ignored bits on each arrival. */
		CHECK(accepted(&p, 1, 0x123, 0, 0) && accepted(&p, 1, 0x124, 0, 1));
	}
	PASS("masked superset admits both required IDs after repeated accepted-ID overwrite");

	entry(&r, 0, 0x18daf110, 0x1fffffff, 1, 2, 1);
	CHECK(!omam_flexcan_compile(&r, 12, &p));
	CHECK(p.id[0] == 0x18daf110 && p.mask[0] == 0xdfffffff && p.control[0] == 0x04300000);
	CHECK(accepted(&p, 12, 0x18daf110, 1, 1));
	CHECK(!accepted(&p, 12, 0x18daf110, 1, 0));
	CHECK(!accepted(&p, 12, 0x18daf111, 1, 1));
	PASS("extended diagnostic ID and remote-only literal register vector");

	entry(&r, 0, 0x18f00000, 0x1fff0000, 1, 0, 3);
	entry(&r, 1, 0x321, 0x7ff, 0, 0, 2);
	CHECK(!omam_flexcan_compile(&r, 12, &p));
	for (id = 0; id < 65536; id++) CHECK(accepted(&p, 12, 0x18f00000 | id, 1, id & 1));
	CHECK(accepted(&p, 12, 0x321, 0, 0));
	CHECK(!accepted(&p, 12, 0x321, 1, 0));
	CHECK(!accepted(&p, 12, 0x18f10000, 1, 0));
	PASS("J1939 masked source/destination group plus independent standard alias");

	r = policy(OMAM_FLEXCAN_ACCEPT_ALL);
	CHECK(!omam_flexcan_compile(&r, 62, &p) && p.count == 62);
	for (id = 0; id < 2048; id++) for (format = 0; format < 2; format++) for (remote = 0; remote < 2; remote++)
		CHECK(accepted(&p, 62, id, format, remote));
	CHECK(accepted(&p, 62, 0x1fffffff, 1, 1));
	PASS("stock accept-all preserves both formats and both RTR values");

	r = policy(OMAM_FLEXCAN_ACCEPT_NONE);
	CHECK(!omam_flexcan_compile(&r, 12, &p) && p.count == 0);
	for (id = 0; id < 2048; id++) CHECK(!accepted(&p, 12, id, 0, 0));
	PASS("accept-none produces no active RX mailbox");

	r = policy(OMAM_FLEXCAN_SELECTIVE);
	entry(&r, 0, 0x100, 0x700, 0, 0, 62);
	CHECK(!omam_flexcan_compile(&r, 62, &p));
	CHECK(omam_flexcan_compile(&r, 12, &p) == -ENOSPC);
	r.entries[0].replicas = htole16(12);
	CHECK(!omam_flexcan_compile(&r, 12, &p));
	entry(&r, 1, 0x200, 0x700, 0, 0, 1);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -ENOSPC);
	CHECK(omam_flexcan_compile(&r, 0, &p) == -EOPNOTSUPP);
	PASS("shared replica/filter capacity is enforced for 62 Classic and 12 FD slots");

	r = policy(OMAM_FLEXCAN_SELECTIVE);
	entry(&r, 0, 0x121, 0x7f8, 0, 0, 1);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	entry(&r, 0, 0x800, 0x7ff, 0, 0, 1);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	entry(&r, 0, 0, 0, 2, 0, 1);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	entry(&r, 0, 0, 0, 0, 3, 1);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	entry(&r, 0, 0, 0, 0, 0, 0);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	PASS("noncanonical IDs, unsupported format/RTR and zero replicas are rejected");

	entry(&r, 0, 0, 0, 0, 0, 1);
	r.entries[0].frame_kind = OMAM_FLEXCAN_FRAME_CLASSIC;
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EOPNOTSUPP);
	r.entries[0].frame_kind = OMAM_FLEXCAN_FRAME_FD;
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EOPNOTSUPP);
	PASS("unsupported EDL-only hardware selection is truthful");

	r = policy(OMAM_FLEXCAN_ACCEPT_ALL);
	r.entries[61].reserved[2] = 1;
	CHECK(omam_flexcan_compile(&r, 62, &p) == -EINVAL);
	r = policy(3);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	r = policy(OMAM_FLEXCAN_SELECTIVE);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	r.entry_count = htole32(63);
	CHECK(omam_flexcan_compile(&r, 12, &p) == -EINVAL);
	PASS("unused bytes and malformed modes/counts cannot carry hidden state");

	r = policy(0);
	r.command = htole32(OMAM_FLEXCAN_REPLACE);
	r.controller_instance = htole64(91);
	r.owner_epoch = htole64(10);
	r.expected_policy_generation = htole64(5);
	r.expected_capability_generation = htole64(7);
	r.request_id = htole64(100);
	previous = r;
	CHECK(!omam_flexcan_authorize(&state, &r, 30, &previous));
	CHECK(omam_flexcan_authorize(&state, &r, 31, &previous) == -EPERM);
	r.controller_instance = htole64(92);
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -ESTALE);
	r = previous; r.owner_epoch = htole64(9);
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -EPERM);
	PASS("controller instance, socket cookie and epoch protect reused ifindex/portid");

	r = previous; r.expected_policy_generation = htole64(4);
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -ESTALE);
	r = previous; r.expected_capability_generation = htole64(6);
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -ESTALE);
	r = previous; r.request_id = 0;
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -ESTALE);
	PASS("stale generations and zero request ID are rejected before mutation");

	r = previous;
	state.replay_id = 100; state.replay_cookie = 30;
	state.policy_generation = 6;
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == 1);
	r.payload_digest[3] ^= 1;
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -EALREADY);
	r = previous; r.request_id = htole64(99);
	CHECK(omam_flexcan_authorize(&state, &r, 30, &previous) == -ESTALE);
	PASS("exact last replay is idempotent; changed bytes and older IDs are refused");

	CHECK(omam_flexcan_cleanup_matches(&state, 10));
	state.owner_epoch = 11;
	CHECK(!omam_flexcan_cleanup_matches(&state, 10));
	CHECK(!omam_flexcan_cleanup_matches(&state, 0));
	state.owner_epoch = 0;
	r = previous; r.command = htole32(OMAM_FLEXCAN_CLAIM); r.owner_epoch = 0;
	r.request_id = htole64(101); r.expected_policy_generation = htole64(6);
	CHECK(!omam_flexcan_authorize(&state, &r, 31, &previous));
	state.owner_epoch = 12;
	CHECK(omam_flexcan_authorize(&state, &r, 31, &previous) == -EBUSY);
	PASS("deferred death cleanup cannot erase a replacement owner; claim is exclusive");

	r = policy(OMAM_FLEXCAN_SELECTIVE); previous = policy(OMAM_FLEXCAN_ACCEPT_NONE);
	h = (struct hardware){0};
	CHECK(!omam_flexcan_transition(&io, &h, &r, &previous, &recovery));
	CHECK(h.calls == 3 && h.running && h.mode == 2 && recovery == 0);
	PASS("one complete successful frozen transaction");

	for (id = 1; id <= 3; id++) {
		h = (struct hardware){0}; h.fail[id] = -EIO;
		CHECK(omam_flexcan_transition(&io, &h, &r, &previous, &recovery) == -EIO);
		CHECK(h.calls == id + 3 && h.running && h.mode == 1 && recovery == OMAM_FLEXCAN_RECOVERY_PREVIOUS);
	}
	PASS("failure at freeze/program/unfreeze restores previous complete policy");

	h = (struct hardware){0}; h.fail[3] = -ETIMEDOUT; h.fail[6] = -EIO;
	CHECK(omam_flexcan_transition(&io, &h, &r, &previous, &recovery) == -ETIMEDOUT);
	CHECK(h.calls == 9 && h.running && h.mode == 0 && recovery == OMAM_FLEXCAN_RECOVERY_ACCEPT_ALL);
	PASS("failed rollback establishes accept-all and preserves original error");

	h = (struct hardware){0}; h.fail[3] = -ETIMEDOUT; h.fail[6] = -EIO; h.fail[9] = -EIO;
	CHECK(omam_flexcan_transition(&io, &h, &r, &previous, &recovery) == -ETIMEDOUT);
	CHECK(h.calls == 9 && !h.running && recovery == OMAM_FLEXCAN_RECOVERY_UNAVAILABLE);
	PASS("failed fallback stops at bounded unavailable outcome");

	h = (struct hardware){0}; h.fail[1] = -ETIMEDOUT; h.fail[2] = -ETIMEDOUT; h.fail[3] = -ETIMEDOUT;
	CHECK(omam_flexcan_transition(&io, &h, &r, &previous, &recovery) == -ETIMEDOUT);
	CHECK(h.calls == 3 && recovery == OMAM_FLEXCAN_RECOVERY_UNAVAILABLE);
	PASS("no writes or unfreeze follow an unsuccessful freeze");
	printf("1..%u\n", cases);
	return cases == 20 ? 0 : 1;
}
