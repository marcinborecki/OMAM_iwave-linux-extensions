/* SPDX-License-Identifier: GPL-2.0 */
/* This is appended after the exact kernel macro by run_rx_busy.py. */
static unsigned int busy_reads;
static unsigned int reads;
static unsigned int delay_calls;
static unsigned int delay_total;
static unsigned int cases;

static unsigned int mailbox_read(void)
{
	reads++;
	return reads <= busy_reads ? 0x01000000U : 0x02000000U;
}
static void recorded_delay(unsigned int us)
{
	delay_calls++;
	delay_total += us;
}
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)
#define PASS(name) do { printf("ok %u - %s\n", ++cases, name); } while (0)

static int observe(unsigned int busy, unsigned int *value)
{
	busy_reads = busy;
	reads = delay_calls = delay_total = 0;
	return read_poll_timeout_atomic(mailbox_read, *value,
					!(*value & 0x01000000U),
					OMAM_FLEXCAN_RX_POLL_DELAY_US,
					OMAM_FLEXCAN_RX_POLL_TIMEOUT_US, false);
}

int main(void)
{
	unsigned int value;
	puts("TAP version 13");
	CHECK(!observe(0, &value));
	CHECK(reads == 1 && delay_calls == 0 && delay_total == 0 && value == 0x02000000U);
	PASS("ready mailbox has one read and no delay");
	CHECK(!observe(1, &value));
	CHECK(reads == 2 && delay_calls == 1 && delay_total == 1 && value == 0x02000000U);
	PASS("one busy observation waits one microsecond of recorded delay");
	CHECK(!observe(249, &value));
	CHECK(reads == 250 && delay_calls == 249 && delay_total == 249);
	PASS("readiness before the budget is accepted");
	CHECK(observe(UINT_MAX, &value) == -ETIMEDOUT);
	CHECK(reads <= 252 && delay_calls <= 250 && delay_total <= 250 && value == 0x01000000U);
	PASS("stuck RX_BUSY is bounded by 252 reads and 250 microseconds of recorded delay");
	printf("1..%u\n", cases);
	return cases == 4 ? 0 : 1;
}
