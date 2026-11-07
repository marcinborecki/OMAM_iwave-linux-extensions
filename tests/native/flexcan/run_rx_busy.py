#!/usr/bin/env python3
"""Test the exact pinned kernel poll macro and the driver's shared policy constants."""
from pathlib import Path
import argparse
import hashlib
import subprocess
import signal
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/kernel"))
from process_group import stop


def run(command, timeout):
    child = subprocess.Popen(command, start_new_session=True)
    try:
        code = child.wait(timeout=timeout)
        if code:
            raise subprocess.CalledProcessError(code, command)
    finally:
        stop(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-source", type=Path, required=True)
    args = parser.parse_args()
    header = args.kernel_source / "include/linux/iopoll.h"
    data = header.read_bytes()
    text = data.decode()
    first = text.index("#define read_poll_timeout_atomic(")
    last = text.index("\n})", first) + 3
    macro = text[first:last]
    preamble = """#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <limits.h>
#include <errno.h>
#include \"flexcan-policy.h\"
typedef uint64_t u64;
typedef int64_t s64;
#define NSEC_PER_USEC 1000
static void recorded_delay(unsigned int us);
#define udelay(us) recorded_delay(us)
#define cpu_relax() ((void)0)
"""
    with tempfile.TemporaryDirectory(prefix="omam-rx-busy-test-") as temporary:
        work = Path(temporary)
        source = work / "rx_busy_test.c"
        source.write_text(preamble + macro + "\n" + Path(__file__).with_name("rx_busy_poll_test.c").read_text())
        binary = work / "rx_busy_test"
        run(["cc", "-D_DEFAULT_SOURCE", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
             "-fsanitize=undefined", "-fno-sanitize-recover=all",
             "-I" + str(ROOT / "components/flexcan-controller-policy/include/uapi"), "-I" + str(ROOT / "components/flexcan-controller-policy"),
             str(source), "-o", str(binary)], 60)
        run([str(binary)], 10)
    print("# kernel iopoll.h sha256 " + hashlib.sha256(data).hexdigest())


def interrupted(signum, frame):
    raise KeyboardInterrupt("RX_BUSY check interrupted")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    main()
