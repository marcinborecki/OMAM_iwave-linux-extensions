#!/usr/bin/env python3
"""Bounded cleanup for a process group created by this invocation with setsid()."""
from __future__ import annotations

import os
import signal
import subprocess
import time


def stop(process: subprocess.Popen, grace: float = 5.0) -> None:
    """Stop the entire owned group, including descendants of an exited leader.

    The caller must have created this exact Popen with start_new_session=True.
    Checking only Popen.poll() loses descendants when their leader exits first.
    No process-name matching or unrelated cgroup/process operation is performed.
    """
    if grace <= 0 or grace > 5:
        raise ValueError("Cleanup grace must be greater than zero and at most five seconds")
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=grace)
        return
    deadline = time.monotonic() + grace
    while True:
        process.poll()  # Reap the owned leader, if it has already exited.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(min(remaining, 0.05))
    process.wait(timeout=grace)
