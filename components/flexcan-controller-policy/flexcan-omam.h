/* SPDX-License-Identifier: GPL-2.0 */
#ifndef FLEXCAN_OMAM_H
#define FLEXCAN_OMAM_H

#include <linux/atomic.h>
#include <linux/list.h>
#include <linux/mutex.h>
#include <linux/workqueue.h>
#include "flexcan-policy.h"

struct sock;
struct flexcan_priv;
struct net_device;

struct flexcan_omam {
	/* RTNL stabilizes netdev identity; lock also serializes PM and restart work. */
	struct mutex lock;
	struct list_head node;
	struct work_struct cleanup;
	struct net_device *dev;
	struct sock *owner_sock;
	struct omam_flexcan_identity identity;
	struct omam_flexcan_request policy;
	struct omam_flexcan_request replay;
	u64 cleanup_epoch;
	u64 gaps;
	u32 seen_ctrlmode;
	u32 seen_quirks;
	u32 capacity;
	u32 applied_mode;
	u32 recovery;
	int last_error;
	int replay_result;
	bool readback;
	bool registered;
	bool suspended;
	bool closing;
	atomic_t rx_fault;
	atomic64_t rx_busy_failures;
};

void flexcan_omam_init(struct net_device *dev);
void flexcan_omam_register(struct net_device *dev);
void flexcan_omam_unregister(struct net_device *dev);
void flexcan_omam_refresh(struct net_device *dev);
int flexcan_omam_family_init(void);
void flexcan_omam_family_exit(void);
/* Caller holds omam.lock. Called only with controller clocks available. */
int flexcan_omam_program(struct net_device *dev,
			const struct omam_flexcan_request *policy);
int flexcan_omam_apply(struct net_device *dev,
		      const struct omam_flexcan_request *policy);
int flexcan_omam_reapply(struct net_device *dev);
void flexcan_omam_unavailable(struct net_device *dev);
#endif
