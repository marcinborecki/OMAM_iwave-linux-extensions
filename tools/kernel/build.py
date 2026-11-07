#!/usr/bin/env python3
"""Bounded native ARM64 kernel build. Run inside the documented systemd resource scope."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from apply import apply, energy
from process_group import stop

REPO = Path(__file__).resolve().parents[2]
RESERVE = 500 * 1024**2
SRCR = "02cf250c2e6bd375c11dae324ea145a39015d41b"


def sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def resource_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--allowed-cpus", required=True, help="explicit comma-separated host CPU numbers")
    parser.add_argument("--cpu-percent", type=int, default=150)
    parser.add_argument("--memory-gib", type=int, default=6)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--nice-min", type=int, default=10)


def constraints(args) -> tuple[Path, dict]:
    try:
        allowed = {int(cpu) for cpu in args.allowed_cpus.split(",")}
    except ValueError as error:
        raise ValueError("allowed-cpus must list CPU numbers") from error
    if (not allowed or min(allowed) < 0 or args.cpu_percent <= 0 or args.memory_gib <= 0
            or not 1 <= args.jobs <= len(allowed) or not 0 <= args.nice_min <= 19):
        raise ValueError("Use positive bounded resources, jobs within allowed CPUs and nice-min 0..19")
    path = next(line.split(":", 2)[2] for line in Path("/proc/self/cgroup").read_text().splitlines()
                if line.startswith("0::"))
    group = Path("/sys/fs/cgroup") / path.lstrip("/")
    result = {name: (group / name).read_text().strip()
              for name in ("cpu.max", "memory.max", "memory.swap.max")}
    quota, period = result["cpu.max"].split()
    affinity = sorted(os.sched_getaffinity(0))
    if quota == "max" or int(quota) * 100 > int(period) * args.cpu_percent:
        raise ValueError("CPU cgroup quota exceeds the explicit cpu-percent bound")
    if result["memory.max"] == "max" or int(result["memory.max"]) > args.memory_gib * 1024**3:
        raise ValueError("Memory cgroup limit exceeds the explicit memory-gib bound")
    if result["memory.swap.max"] != "0" or not set(affinity) <= allowed:
        raise ValueError("Use the explicit allowed CPUs and a no-swap cgroup")
    if os.getpriority(os.PRIO_PROCESS, 0) < args.nice_min:
        raise ValueError("Process priority exceeds the explicit nice-min bound")
    result.update(affinity=affinity, cgroup=path, jobs=args.jobs,
                  allowed_cpus=sorted(allowed), cpu_percent=args.cpu_percent,
                  memory_gib=args.memory_gib, nice_min=args.nice_min)
    return group, result


def identity(source: Path, baseline: Path, toolchain: Path, release: str) -> dict:
    head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                          capture_output=True, check=True, timeout=30).stdout.decode().strip()
    if head != SRCR:
        raise ValueError("Wrong kernel SRCREV")
    apply(source, check_only=True)
    diff = subprocess.run(["git", "-C", str(source), "diff", "--binary", "HEAD"],
                          capture_output=True, check=True, timeout=30).stdout
    additions = subprocess.run(["git", "-C", str(source), "ls-files", "--others", "--exclude-standard", "-z"],
                               capture_output=True, check=True, timeout=30).stdout.split(b"\0")
    added_hashes = {name.decode(): sha(source / name.decode()) for name in additions if name}
    energy_identity = energy(source, check_only=True)
    return {
        **energy_identity,
        "source_added_files_sha256": added_hashes,
        "srcrev": head, "source_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "flexcan_manifest_sha256": sha(REPO / "components/flexcan-controller-policy/manifest.json"),
        "uapi_sha256": sha(REPO / "components/flexcan-controller-policy/include/uapi/linux/omam_flexcan.h"),
        "stock_config_sha256": sha(baseline / "inputs/stock.config"),
        "vendor_receipt_sha256": sha(baseline / "inputs/manifest.json"),
        "toolchain_receipt_sha256": sha(baseline / "toolchain/manifest.json"),
        "compiler_sha256": sha(toolchain / "usr/bin/aarch64-linux-gnu-gcc-14"),
        "kernel_release": release,
        "build_tool_sha256": sha(Path(__file__)),
        "apply_tool_sha256": sha(Path(__file__).with_name("apply.py")),
        "process_supervisor_sha256": sha(Path(__file__).with_name("process_group.py")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "baseline", "toolchain", "work", "output", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--phase", choices=("driver", "full"), required=True)
    resource_arguments(parser)
    args = parser.parse_args()
    if not re.fullmatch(r"6\.12\.34-[a-z0-9][a-z0-9-]{0,40}", args.release):
        parser.error("Use a clear candidate kernel release suffix for the pinned Linux 6.12.34 base")
    if os.uname().machine != "aarch64":
        parser.error("This build route is native ARM64")
    for name in ("source", "baseline", "toolchain", "work", "output", "receipt"):
        setattr(args, name, getattr(args, name).resolve())
    args.work.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.work).free < 3 * 1024**3 + RESERVE:
        parser.error("Need the measured native work/output allowance (3 GiB) plus 500 MiB reserve")
    group, resources = constraints(args)
    candidate = identity(args.source, args.baseline, args.toolchain, args.release)
    receipt = json.loads(args.receipt.read_text()) if args.receipt.exists() else {
        "schema": "iwave.rel33.kernel-build.v2", "phases": {}, "physical_loading": "NOT_RUN",
        "hardware_readback": "NOT_RUN", "product_tests_executed": 0,
    }
    receipt.update(status="BUILDING", inputs=candidate, source=str(args.source), work=str(args.work),
                   output=str(args.output), resources=resources)
    write_json(args.receipt, receipt)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PATH"] = str(args.toolchain / "usr/bin") + ":" + env["PATH"] + ":/usr/sbin:/sbin"
    env["LD_LIBRARY_PATH"] = str(args.toolchain / "usr/lib/aarch64-linux-gnu")
    env["BISON_PKGDATADIR"] = str(args.toolchain / "usr/share/bison")
    env["M4"] = str(args.toolchain / "usr/bin/m4")
    make = ["make", "-C", str(args.source), "O=" + str(args.work), "ARCH=arm64",
            "CC=" + str(args.toolchain / "usr/bin/aarch64-linux-gnu-gcc-14"), "HOSTCC=/usr/bin/gcc",
            "HOSTCFLAGS=-O2 -I" + str(args.toolchain / "usr/include"),
            "HOSTLDFLAGS=-L" + str(args.toolchain / "usr/lib/aarch64-linux-gnu") +
            " -Wl,-rpath," + str(args.toolchain / "usr/lib/aarch64-linux-gnu"),
            "KERNELRELEASE=" + args.release, "KCFLAGS=-ffile-prefix-map=" + str(args.baseline) + "/=", "-j" + str(args.jobs)]
    phase_log = args.work / (args.phase + ".log")
    phase_log.unlink(missing_ok=True)
    started = time.monotonic()
    steps = []

    def run(label, command, deadline):
        began = time.monotonic()
        entry = {"name": label, "argv": command, "timeout_seconds": deadline}
        steps.append(entry)
        print("Starting " + label, flush=True)
        with phase_log.open("ab") as log:
            child = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            try:
                until = began + deadline
                while True:
                    try:
                        code = child.wait(timeout=min(30, max(.1, until - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= until:
                            raise TimeoutError(label + " exceeded its bounded deadline")
                        free = shutil.disk_usage(args.work).free
                        receipt["free_bytes_min"] = min(free, receipt.get("free_bytes_min", free))
                        if free < RESERVE:
                            raise RuntimeError("Build stopped to preserve the 500 MiB disk reserve")
                        receipt["current_phase"] = label
                        receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
                        write_json(args.receipt, receipt)
                entry["exit_code"] = code
                entry["elapsed_seconds"] = round(time.monotonic() - began, 3)
                if code:
                    raise RuntimeError(label + " failed with exit " + str(code))
            finally:
                stop(child)
                entry.setdefault("exit_code", child.returncode)
                entry.setdefault("elapsed_seconds", round(time.monotonic() - began, 3))
        print(label + " exit 0", flush=True)

    try:
        config = args.work / ".config"
        marker = args.work / "iwave-input.json"
        if not config.exists():
            shutil.copyfile(args.baseline / "inputs/stock.config", config)
            write_json(marker, {"stock_config_sha256": candidate["stock_config_sha256"], "release": args.release})
            run("olddefconfig", [*make, "olddefconfig"], 300)
        elif not marker.exists() or json.loads(marker.read_text()) != {
                "stock_config_sha256": candidate["stock_config_sha256"], "release": args.release}:
            raise ValueError("Refusing an unowned or mismatched kernel work directory")
        required = ["CONFIG_CAN_FLEXCAN=y", "CONFIG_CRYPTO_LIB_SHA256=y", "CONFIG_MODVERSIONS=y"]
        values = set(config.read_text().splitlines())
        if any(v not in values for v in required):
            raise ValueError("The candidate config lacks a required driver/hash/versioning feature")
        if args.phase == "driver":
            run("flexcan_compile", [*make, "drivers/net/can/flexcan/"], 600)
            run("energy_driver_compile", [*make, "drivers/power/supply/bq256xx_charger.o"], 300)
        else:
            run("image_dtb_modules", [*make, "Image", "freescale/imx91-iwg62h-prgtt.dtb", "modules"], 10800)
        if identity(args.source, args.baseline, args.toolchain, args.release) != candidate:
            raise ValueError("Kernel input identity changed during compilation")
        receipt["status"] = "DRIVER_COMPILED" if args.phase == "driver" else "KERNEL_COMPILED"
        receipt.pop("error", None)
        receipt.pop("diagnostic_tail", None)
        receipt.pop("current_phase", None)
        receipt["config_sha256"] = sha(config)
        receipt["config_delta"] = {
            "removed": sorted(set((args.baseline / "inputs/stock.config").read_text().splitlines()) - values),
            "added": sorted(values - set((args.baseline / "inputs/stock.config").read_text().splitlines())),
        }
        return 0
    except BaseException as error:
        receipt["status"] = "FAILED"
        receipt["error"] = str(error)
        if phase_log.exists():
            receipt["diagnostic_tail"] = phase_log.read_text(errors="replace")[-6000:]
        print(str(error), file=sys.stderr, flush=True)
        return 1
    finally:
        receipt["phases"][args.phase] = {"steps": steps, "inputs": candidate, "elapsed_seconds": round(time.monotonic() - started, 3),
                                        "kind": "incremental" if "initial_full_build" in receipt else "initial"}
        for name in ("memory.peak", "memory.events", "cpu.stat"):
            receipt["resources"][name] = (group / name).read_text().strip()
        receipt["free_bytes_end"] = shutil.disk_usage(args.work).free
        if phase_log.exists():
            receipt["phases"][args.phase]["log_sha256"] = sha(phase_log)
        write_json(args.receipt, receipt)


def interrupted(signum, frame):
    raise KeyboardInterrupt("Native build interrupted")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(main())
