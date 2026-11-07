/* SPDX-License-Identifier: GPL-2.0-only */
/* The runner inserts the actual patched driver hooks, not a second implementation. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

#define BIT(n) (1u << (n))
#define GENMASK(h, l) (((~0u) << (l)) & ((~0u) >> (31u - (h))))
#define BQ25601 2
#define BQ256XX_PART_INFORMATION 11
#define BQ256XX_CHARGER_CONTROL_3 7
enum { SYSTEM_RUNNING, SYSTEM_RESTART, SYSTEM_HALT, SYSTEM_POWER_OFF };
static int system_state;
struct regmap { unsigned int value[12]; int reads, writes, fail_read, fail_write; bool mismatch; };
struct mutex { int held; };
struct device { void *data; };
struct i2c_client { struct device dev; };
struct chip_info { int model_id; };
struct bq256xx_device {
    struct device *dev;
    struct regmap *regmap;
    struct chip_info *chip_info;
    struct mutex lock;
    bool ship_mode_on_poweroff;
};
struct attribute { const char *name; unsigned int mode; };
struct device_attribute {
    struct attribute attr;
    ssize_t (*show)(struct device *, struct device_attribute *, char *);
    ssize_t (*store)(struct device *, struct device_attribute *, const char *, size_t);
};
struct attribute_group { struct attribute **attrs; };
#define DEVICE_ATTR_RW(name) struct device_attribute dev_attr_##name = { \
    { #name, 0644 }, name##_show, name##_store }
static int errors, infos, groups;
static void *dev_get_drvdata(struct device *dev) { return dev->data; }
static void *i2c_get_clientdata(struct i2c_client *client) { return client->dev.data; }
static void mutex_lock(struct mutex *lock) { assert(lock->held == 0); lock->held = 1; }
static void mutex_unlock(struct mutex *lock) { assert(lock->held == 1); lock->held = 0; }
static int kstrtobool(const char *text, bool *value)
{
    if (!strcmp(text, "1") || !strcmp(text, "1\n")) { *value = true; return 0; }
    if (!strcmp(text, "0") || !strcmp(text, "0\n")) { *value = false; return 0; }
    return -EINVAL;
}
static int sysfs_emit(char *buffer, const char *format, ...)
{
    va_list args;
    va_start(args, format);
    int result = vsnprintf(buffer, 32, format, args);
    va_end(args);
    return result;
}
static int regmap_read_bypassed(struct regmap *map, unsigned int reg, unsigned int *value)
{
    assert(reg == 7 || reg == 11);
    map->reads++;
    if (map->fail_read) return -EIO;
    *value = map->value[reg];
    if (map->mismatch && reg == 7) *value &= ~0x20u;
    return 0;
}
static int regmap_update_bits(struct regmap *map, unsigned int reg, unsigned int mask, unsigned int value)
{
    /* TI Table 9-12 is the independent register oracle. No JEITA/reset/detection bit belongs to this operation. */
    assert(reg == 7 && mask == 0x28 && value == 0x28);
    map->writes++;
    if (map->fail_write) return -EIO;
    map->value[reg] = (map->value[reg] & ~mask) | (value & mask);
    return 0;
}
static int devm_device_add_group(struct device *dev, const struct attribute_group *group)
{
    assert(dev && group && group->attrs[0] && group->attrs[1] == NULL);
    assert(!strcmp(group->attrs[0]->name, "ship_mode_on_poweroff"));
    assert(group->attrs[0]->mode == 0644);
    groups++;
    return 0;
}
#define dev_err(dev, ...) do { assert(dev); errors++; } while (0)
#define dev_warn(dev, ...) do { assert(dev); errors++; } while (0)
#define dev_info(dev, ...) do { assert(dev); infos++; } while (0)

/* INSERT_PRODUCTION_HOOKS */

struct fixture {
    struct i2c_client client;
    struct regmap map;
    struct chip_info chip;
    struct bq256xx_device bq;
};
static void initialize(struct fixture *f)
{
    memset(f, 0, sizeof(*f));
    f->chip.model_id = BQ25601;
    f->map.value[11] = 0x10;
    f->map.value[7] = 0x4c;
    f->client.dev.data = &f->bq;
    f->bq.dev = &f->client.dev;
    f->bq.regmap = &f->map;
    f->bq.chip_info = &f->chip;
    system_state = SYSTEM_RUNNING;
    errors = infos = groups = 0;
}
static void arm(struct fixture *f)
{
    assert(ship_mode_on_poweroff_store(f->bq.dev, NULL, "1\n", 2) == 2);
    assert(f->bq.ship_mode_on_poweroff && !f->map.writes);
}
static void default_is_inert(void)
{
    struct fixture f; initialize(&f);
    system_state = SYSTEM_POWER_OFF;
    bq25601_ship_shutdown(&f.client);
    assert(!f.map.writes && f.map.value[7] == 0x4c && !infos && !errors);
}
static void arm_readback_and_cancel_do_not_touch_registers(void)
{
    struct fixture f; char text[32]; initialize(&f); arm(&f);
    assert(ship_mode_on_poweroff_show(f.bq.dev, NULL, text) == 2 && !strcmp(text, "1\n"));
    f.map.fail_read = 1;
    assert(ship_mode_on_poweroff_store(f.bq.dev, NULL, "0", 1) == 1);
    assert(!f.bq.ship_mode_on_poweroff && !f.map.writes);
}
static void only_exact_variant_and_live_part_can_arm(void)
{
    struct fixture f; initialize(&f);
    f.chip.model_id = 3;
    assert(ship_mode_on_poweroff_store(f.bq.dev, NULL, "1", 1) == -EOPNOTSUPP);
    f.chip.model_id = BQ25601; f.map.value[11] = 0x20;
    assert(ship_mode_on_poweroff_store(f.bq.dev, NULL, "1", 1) == -ENODEV);
    f.map.fail_read = 1;
    assert(ship_mode_on_poweroff_store(f.bq.dev, NULL, "1", 1) == -EIO);
    assert(!f.bq.ship_mode_on_poweroff && !f.map.writes);
}
static void restart_and_halt_never_disable_batfet(void)
{
    for (int state = SYSTEM_RESTART; state <= SYSTEM_HALT; state++) {
        struct fixture f; initialize(&f); arm(&f);
        system_state = state; bq25601_ship_shutdown(&f.client);
        assert(!f.map.writes && !f.bq.ship_mode_on_poweroff);
    }
}
static void every_register_byte_keeps_all_unrelated_bits(void)
{
    for (unsigned int before = 0; before <= 255; before++) {
        struct fixture f; initialize(&f); arm(&f); f.map.value[7] = before;
        system_state = SYSTEM_POWER_OFF; bq25601_ship_shutdown(&f.client);
        assert(f.map.value[7] == (before | 0x28u));
        assert((f.map.value[7] & 0xd7u) == (before & 0xd7u));
        assert(f.map.writes == 1 && infos == 1 && !errors && !f.bq.ship_mode_on_poweroff);
        bq25601_ship_shutdown(&f.client);
        assert(f.map.writes == 1);
    }
}
static void changed_part_blocks_shutdown_write(void)
{
    struct fixture f; initialize(&f); arm(&f); f.map.value[11] = 0x20;
    system_state = SYSTEM_POWER_OFF; bq25601_ship_shutdown(&f.client);
    assert(!f.map.writes && errors == 1 && !infos);
}
static void write_failure_is_observable_and_consumes_arm(void)
{
    struct fixture f; initialize(&f); arm(&f); f.map.fail_write = 1;
    system_state = SYSTEM_POWER_OFF; bq25601_ship_shutdown(&f.client);
    assert(f.map.value[7] == 0x4c && errors == 1 && !infos && !f.bq.ship_mode_on_poweroff);
}
static void readback_mismatch_is_not_success(void)
{
    struct fixture f; initialize(&f); arm(&f); f.map.mismatch = true;
    system_state = SYSTEM_POWER_OFF; bq25601_ship_shutdown(&f.client);
    assert(errors == 1 && !infos);
}
static void invalid_or_late_sysfs_write_cannot_arm(void)
{
    struct fixture f; initialize(&f);
    assert(ship_mode_on_poweroff_store(f.bq.dev, NULL, "2", 1) == -EINVAL);
    system_state = SYSTEM_POWER_OFF;
    assert(ship_mode_on_poweroff_store(f.bq.dev, NULL, "1", 1) == -ESHUTDOWN);
    assert(!f.bq.ship_mode_on_poweroff && !f.map.writes);
}
static void feature_is_exposed_only_for_matched_silicon(void)
{
    struct fixture f; initialize(&f); f.map.value[11] = 0;
    bq25601_ship_init(&f.bq); assert(!groups);
    f.map.value[11] = 0x10; bq25601_ship_init(&f.bq); assert(groups == 1);
    f.chip.model_id = 3; bq25601_ship_init(&f.bq); assert(groups == 1);
}
int main(void)
{
    default_is_inert();
    arm_readback_and_cancel_do_not_touch_registers();
    only_exact_variant_and_live_part_can_arm();
    restart_and_halt_never_disable_batfet();
    every_register_byte_keeps_all_unrelated_bits();
    changed_part_blocks_shutdown_write();
    write_failure_is_observable_and_consumes_arm();
    readback_mismatch_is_not_success();
    invalid_or_late_sysfs_write_cannot_arm();
    feature_is_exposed_only_for_matched_silicon();
    puts("{\"executed\":10,\"failed\":0,\"skipped\":0,\"registerValues\":256}");
    return 0;
}
