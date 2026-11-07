// SPDX-License-Identifier: GPL-2.0
/* Bounded per-controller policy control. No CAN data socket is opened here. */
#include <crypto/sha2.h>
#include <linux/can/dev.h>
#include <linux/module.h>
#include <linux/netlink.h>
#include <linux/of.h>
#include <linux/random.h>
#include <linux/rtnetlink.h>
#include <linux/sock_diag.h>
#include <net/genetlink.h>
#include <net/sock.h>
#include "flexcan.h"

static LIST_HEAD(omam_controllers);
static DEFINE_MUTEX(omam_controllers_lock);
static atomic64_t omam_epoch = ATOMIC64_INIT(0);
static struct genl_family omam_family;
static const struct omam_flexcan_request omam_accept_all;

static bool omam_supported(struct net_device *dev)
{
	struct flexcan_priv *priv = netdev_priv(dev);
	u32 flags = FLEXCAN_QUIRK_USE_RX_MAILBOX | FLEXCAN_QUIRK_ENABLE_EACEN_RRS;

	return of_device_is_compatible(priv->dev->of_node, "fsl,imx93-flexcan") &&
	       (priv->devtype_data.quirks & flags) == flags;
}

void flexcan_omam_refresh(struct net_device *dev)
{
	struct flexcan_priv *priv = netdev_priv(dev);
	struct flexcan_omam *s = &priv->omam;

	if (s->seen_ctrlmode != priv->can.ctrlmode ||
	    s->seen_quirks != priv->devtype_data.quirks) {
		s->seen_ctrlmode = priv->can.ctrlmode;
		s->seen_quirks = priv->devtype_data.quirks;
		s->identity.capability_generation++;
	}
	/* Qualified imx93-compatible IP: two 512-byte banks, MB0 erratum and final TX. */
	s->capacity = omam_supported(dev) ?
		2 * (512 / ((priv->can.ctrlmode & CAN_CTRLMODE_FD) ? 72 : 16)) - 2 : 0;
}

static void omam_drop_owner(struct flexcan_omam *s)
{
	if (s->owner_sock) {
		sock_put(s->owner_sock);
		s->owner_sock = NULL;
	}
	s->identity.owner_epoch = 0;
	s->identity.owner_cookie = 0;
}

static void omam_default(struct flexcan_omam *s)
{
	memset(&s->policy, 0, sizeof(s->policy));
	s->identity.policy_generation++;
}

int flexcan_omam_reapply(struct net_device *dev)
{
	struct flexcan_priv *priv = netdev_priv(dev);
	struct flexcan_omam *s = &priv->omam;
	struct omam_flexcan_program program;
	int err;

	flexcan_omam_refresh(dev);
	if (!s->capacity)
		return 0;
		/* Other FlexCAN IPs retain their stock implementation. */
	if (s->owner_sock && sock_flag(s->owner_sock, SOCK_DEAD)) {
		omam_drop_owner(s);
		omam_default(s);
		s->recovery = OMAM_FLEXCAN_RECOVERY_OWNER_LOST;
	}
	err = omam_flexcan_compile(&s->policy, s->capacity, &program);
	if (err) {
		omam_default(s);
		s->recovery = OMAM_FLEXCAN_RECOVERY_CAPACITY;
		s->last_error = err;
	}
	s->gaps++;
	return flexcan_omam_program(dev, &s->policy);
}

static void omam_owner_cleanup(struct work_struct *work)
{
	struct flexcan_omam *s = container_of(work, struct flexcan_omam, cleanup);
	int err;

	rtnl_lock();
	mutex_lock(&s->lock);
	if (s->registered && omam_flexcan_cleanup_matches(&s->identity, s->cleanup_epoch)) {
		err = flexcan_omam_apply(s->dev, &omam_accept_all);
		if (err)
			flexcan_omam_unavailable(s->dev);
		omam_drop_owner(s);
		omam_default(s);
		s->recovery = err ? OMAM_FLEXCAN_RECOVERY_UNAVAILABLE : OMAM_FLEXCAN_RECOVERY_OWNER_LOST;
		s->last_error = err;
		if (err) {
			s->readback = false;
			s->applied_mode = OMAM_FLEXCAN_UNAVAILABLE;
		}
	}
	mutex_unlock(&s->lock);
	rtnl_unlock();
}

static int omam_netlink_release(struct notifier_block *nb, unsigned long event, void *data)
{
	struct netlink_notify *n = data;
	struct flexcan_omam *s;

	if (event != NETLINK_URELEASE || n->protocol != NETLINK_GENERIC)
		return NOTIFY_DONE;
	mutex_lock(&omam_controllers_lock);
	list_for_each_entry(s, &omam_controllers, node) {
		mutex_lock(&s->lock);
		/* The held socket is the identity. A reused portid cannot match a live
		 * replacement socket. Deferred work checks the exact departed epoch.
		 */
		if (s->owner_sock && sock_net(s->owner_sock) == n->net &&
		    sock_flag(s->owner_sock, SOCK_DEAD)) {
			s->cleanup_epoch = s->identity.owner_epoch;
			schedule_work(&s->cleanup);
		}
		mutex_unlock(&s->lock);
	}
	mutex_unlock(&omam_controllers_lock);
	return NOTIFY_DONE;
}

static struct notifier_block omam_notifier = { .notifier_call = omam_netlink_release };

static int omam_netdev_event(struct notifier_block *nb, unsigned long event, void *data)
{
	struct net_device *dev = netdev_notifier_info_to_dev(data);
	struct flexcan_omam *s;

	if (event != NETDEV_UNREGISTER || dev->ethtool_ops != &flexcan_ethtool_ops)
		return NOTIFY_DONE;
	s = &((struct flexcan_priv *)netdev_priv(dev))->omam;
	mutex_lock(&s->lock);
	if (s->registered) {
		/* Namespace moves emit UNREGISTER even when the platform device lives.
		 * The netdev is down here; a new namespace never inherits an old lease.
		 */
		omam_drop_owner(s);
		omam_default(s);
		s->identity.instance = get_random_u64() ?: 1;
		s->identity.replay_id = 0;
		s->identity.replay_cookie = 0;
		s->readback = false;
		s->applied_mode = OMAM_FLEXCAN_UNAVAILABLE;
	}
	mutex_unlock(&s->lock);
	return NOTIFY_DONE;
}
static struct notifier_block omam_netdev_notifier = { .notifier_call = omam_netdev_event };


void flexcan_omam_init(struct net_device *dev)
{
	struct flexcan_priv *priv = netdev_priv(dev);
	struct flexcan_omam *s = &priv->omam;

	mutex_init(&s->lock);
	INIT_LIST_HEAD(&s->node);
	INIT_WORK(&s->cleanup, omam_owner_cleanup);
	s->dev = dev;
	s->identity.instance = get_random_u64() ?: 1;
	s->identity.policy_generation = 1;
	s->identity.capability_generation = 1;
	s->seen_ctrlmode = priv->can.ctrlmode;
	s->seen_quirks = priv->devtype_data.quirks;
	s->applied_mode = OMAM_FLEXCAN_UNAVAILABLE;
	atomic_set(&s->rx_fault, 0);
	atomic64_set(&s->rx_busy_failures, 0);
	flexcan_omam_refresh(dev);
}

void flexcan_omam_register(struct net_device *dev)
{
	struct flexcan_omam *s = &((struct flexcan_priv *)netdev_priv(dev))->omam;

	mutex_lock(&omam_controllers_lock);
	s->registered = true;
	list_add_tail(&s->node, &omam_controllers);
	mutex_unlock(&omam_controllers_lock);
}

void flexcan_omam_unregister(struct net_device *dev)
{
	struct flexcan_omam *s = &((struct flexcan_priv *)netdev_priv(dev))->omam;

	mutex_lock(&omam_controllers_lock);
	mutex_lock(&s->lock);
	s->registered = false;
	list_del_init(&s->node);
	mutex_unlock(&s->lock);
	mutex_unlock(&omam_controllers_lock);
	cancel_work_sync(&s->cleanup);
	mutex_lock(&s->lock);
	omam_drop_owner(s);
	mutex_unlock(&s->lock);
}

static void omam_response(struct net_device *dev, struct omam_flexcan_response *out,
			  int result, bool replay)
{
	struct flexcan_priv *priv = netdev_priv(dev);
	struct flexcan_omam *s = &priv->omam;
	u32 flags = s->capacity ? OMAM_FLEXCAN_F_SUPPORTED : 0;
	bool available = netif_running(dev) && !s->suspended &&
		priv->can.state < CAN_STATE_BUS_OFF && !atomic_read(&s->rx_fault);

	if (netif_running(dev))
		flags |= OMAM_FLEXCAN_F_RUNNING;
	if (s->identity.owner_epoch)
		flags |= OMAM_FLEXCAN_F_OWNED;
	if (available && s->readback)
		flags |= OMAM_FLEXCAN_F_READBACK;
	if (replay)
		flags |= OMAM_FLEXCAN_F_REPLAY;
	if (atomic_read(&s->rx_fault))
		flags |= OMAM_FLEXCAN_F_RX_BUSY_FAULT;
	out->abi = cpu_to_le32(OMAM_FLEXCAN_VERSION);
	out->ifindex = cpu_to_le32(dev->ifindex);
	out->result = cpu_to_le32(result);
	out->flags = cpu_to_le32(flags);
	out->controller_instance = cpu_to_le64(s->identity.instance);
	out->owner_epoch = cpu_to_le64(s->identity.owner_epoch);
	out->policy_generation = cpu_to_le64(s->identity.policy_generation);
	out->capability_generation = cpu_to_le64(s->identity.capability_generation);
	out->receive_gap_sequence = cpu_to_le64(s->gaps);
	out->profile_generation = s->policy.profile_generation;
	out->last_request_id = cpu_to_le64(s->identity.replay_id);
	out->rx_busy_failures = cpu_to_le64(atomic64_read(&s->rx_busy_failures));
	out->mode = s->policy.mode;
	out->applied_mode = cpu_to_le32(available ? s->applied_mode : OMAM_FLEXCAN_UNAVAILABLE);
	out->rx_capacity = cpu_to_le32(s->capacity);
	out->entry_count = s->policy.entry_count;
	out->ctrlmode = cpu_to_le32(priv->can.ctrlmode);
	out->can_state = cpu_to_le32(priv->can.state);
	out->recovery = cpu_to_le32(s->recovery);
	out->last_error = cpu_to_le32(s->last_error);
	memcpy(out->profile_digest, s->policy.profile_digest, 32);
	memcpy(out->payload_digest, s->policy.payload_digest, 32);
	memcpy(out->entries, s->policy.entries, sizeof(out->entries));
}

static int omam_do_command(struct sk_buff *skb, struct genl_info *info)
{
	struct omam_flexcan_request *req;
	struct omam_flexcan_response *out;
	struct omam_flexcan_program program;
	struct flexcan_priv *priv;
	struct flexcan_omam *s;
	struct net_device *dev;
	struct sk_buff *reply;
	struct nlattr *attr;
	u8 digest[32], expected[32];
	u64 cookie;
	void *header;
	int err, remaining, attrs = 0;
	bool replay = false;
	u32 command = info->genlhdr->cmd;

	/* Reject duplicate/flagged/unknown attributes, including a response attribute. */
	nla_for_each_attr(attr, genlmsg_data(info->genlhdr), genlmsg_len(info->genlhdr), remaining) {
		if (attr->nla_type != OMAM_FLEXCAN_A_REQUEST || ++attrs != 1)
			return -EINVAL;
	}
	if (remaining || attrs != 1 || info->genlhdr->version != OMAM_FLEXCAN_VERSION)
		return -EINVAL;
	req = kmemdup(nla_data(info->attrs[OMAM_FLEXCAN_A_REQUEST]), sizeof(*req), GFP_KERNEL);
	out = kzalloc(sizeof(*out), GFP_KERNEL);
	if (!req || !out) {
		err = -ENOMEM;
		goto out_free;
	}
	if (le32_to_cpu(req->abi) != OMAM_FLEXCAN_VERSION ||
	    le32_to_cpu(req->command) != command || req->reserved) {
		err = -EINVAL;
		goto out_free;
	}
	if (command <= OMAM_FLEXCAN_GET_POLICY) {
		if (memchr_inv(&req->controller_instance, 0,
			       sizeof(*req) - offsetof(struct omam_flexcan_request, controller_instance))) {
			err = -EINVAL;
			goto out_free;
		}
	} else {
		memcpy(expected, req->payload_digest, 32);
		memset(req->payload_digest, 0, 32);
		sha256((u8 *)req, sizeof(*req), digest);
		memcpy(req->payload_digest, expected, 32);
		if (memcmp(digest, expected, 32)) {
			err = -EBADMSG;
			goto out_free;
		}
	}
	cookie = sock_gen_cookie(NETLINK_CB(skb).sk);
	rtnl_lock();
	dev = __dev_get_by_index(genl_info_net(info), le32_to_cpu(req->ifindex));
	if (!dev || dev->ethtool_ops != &flexcan_ethtool_ops) {
		err = -ENODEV;
		goto out_rtnl;
	}
	priv = netdev_priv(dev);
	s = &priv->omam;
	mutex_lock(&s->lock);
	if (!s->registered) {
		err = -ENODEV;
		goto out_unlock;
	}
	flexcan_omam_refresh(dev);
	if (command <= OMAM_FLEXCAN_GET_POLICY) {
		err = 0;
		goto respond;
	}
	if (!s->capacity) {
		err = -EOPNOTSUPP;
		goto respond;
	}
	err = omam_flexcan_authorize(&s->identity, req, cookie, &s->replay);
	if (err == 1) {
		replay = true;
		err = s->replay_result;
		goto respond;
	}
	if (err)
		goto respond;
	if (command == OMAM_FLEXCAN_CLAIM || command >= OMAM_FLEXCAN_CLEAR) {
		if (req->mode || req->entry_count || req->profile_generation ||
		    memchr_inv(req->profile_digest, 0, 32)) {
			err = -EINVAL;
			goto respond;
		}
	}
	err = omam_flexcan_compile(req, s->capacity, &program);
	if (err)
		goto respond;
	switch (command) {
	case OMAM_FLEXCAN_CLAIM:
		if (s->suspended) {
			err = -EBUSY;
			break;
		}
		sock_hold(NETLINK_CB(skb).sk);
		s->owner_sock = NETLINK_CB(skb).sk;
		s->identity.owner_cookie = cookie;
		s->identity.owner_epoch = atomic64_inc_return(&omam_epoch);
		break;
	case OMAM_FLEXCAN_VALIDATE:
		break;
	case OMAM_FLEXCAN_REPLACE:
	case OMAM_FLEXCAN_CLEAR:
	case OMAM_FLEXCAN_RELEASE:
		err = flexcan_omam_apply(dev, req);
		if (!err) {
			memcpy(&s->policy, req, sizeof(*req));
			s->identity.policy_generation++;
			if (command == OMAM_FLEXCAN_RELEASE)
				omam_drop_owner(s);
		}
		break;
	default:
		err = -EOPNOTSUPP;
	}
	/* One bounded replay slot, with full bytes and the kernel socket cookie. */
	memcpy(&s->replay, req, sizeof(*req));
	s->identity.replay_id = le64_to_cpu(req->request_id);
	s->identity.replay_cookie = cookie;
	s->replay_result = err;
 respond:
	omam_response(dev, out, err, replay);
	err = 0;
 out_unlock:
	mutex_unlock(&s->lock);
 out_rtnl:
	rtnl_unlock();
	if (err)
		goto out_free;
	reply = genlmsg_new(nla_total_size(sizeof(*out)), GFP_KERNEL);
	if (!reply) {
		err = -ENOMEM;
		goto out_free;
	}
	header = genlmsg_put_reply(reply, info, &omam_family, 0, command);
	if (!header || nla_put(reply, OMAM_FLEXCAN_A_RESPONSE, sizeof(*out), out)) {
		nlmsg_free(reply);
		err = -EMSGSIZE;
		goto out_free;
	}
	genlmsg_end(reply, header);
	err = genlmsg_reply(reply, info);
 out_free:
	kfree(out);
	kfree(req);
	return err;
}

static const struct nla_policy omam_attrs[3] = {
	[OMAM_FLEXCAN_A_REQUEST] = NLA_POLICY_EXACT_LEN(sizeof(struct omam_flexcan_request)),
	[OMAM_FLEXCAN_A_RESPONSE] = { .type = NLA_REJECT },
};
#define OMAM_OP(c, f) { .cmd = (c), .flags = (f), .doit = omam_do_command }
static const struct genl_ops omam_ops[] = {
	OMAM_OP(OMAM_FLEXCAN_GET_CAPS, 0),
	OMAM_OP(OMAM_FLEXCAN_GET_POLICY, 0),
	OMAM_OP(OMAM_FLEXCAN_CLAIM, GENL_UNS_ADMIN_PERM),
	OMAM_OP(OMAM_FLEXCAN_VALIDATE, GENL_UNS_ADMIN_PERM),
	OMAM_OP(OMAM_FLEXCAN_REPLACE, GENL_UNS_ADMIN_PERM),
	OMAM_OP(OMAM_FLEXCAN_CLEAR, GENL_UNS_ADMIN_PERM),
	OMAM_OP(OMAM_FLEXCAN_RELEASE, GENL_UNS_ADMIN_PERM),
};
static struct genl_family omam_family = {
	.name = OMAM_FLEXCAN_FAMILY,
	.version = OMAM_FLEXCAN_VERSION,
	.maxattr = OMAM_FLEXCAN_A_RESPONSE,
	.policy = omam_attrs,
	.netnsok = true,
	.module = THIS_MODULE,
	.ops = omam_ops,
	.n_ops = ARRAY_SIZE(omam_ops),
};

int flexcan_omam_family_init(void)
{
	int err = netlink_register_notifier(&omam_notifier);

	if (err)
		return err;
	err = register_netdevice_notifier(&omam_netdev_notifier);
	if (err)
		goto out_notifier;
	err = genl_register_family(&omam_family);
	if (!err)
		return 0;
	unregister_netdevice_notifier(&omam_netdev_notifier);
 out_notifier:
	netlink_unregister_notifier(&omam_notifier);
	return err;
}
void flexcan_omam_family_exit(void)
{
	genl_unregister_family(&omam_family);
	unregister_netdevice_notifier(&omam_netdev_notifier);
	netlink_unregister_notifier(&omam_notifier);
}
