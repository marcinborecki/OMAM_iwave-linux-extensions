#!/usr/bin/env python3
"""Exercise the actual patched BQ25601 hooks with a native regmap/sysfs boundary; no kernel or hardware mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="the frozen pre-patch bq256xx_charger.c")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    manifest = json.loads((here / "source.json").read_text())
    original = args.source.read_bytes()
    patch = (here / "bq25601-ship-on-poweroff.patch").read_bytes()
    if digest(original) != manifest["preSha256"] or digest(patch) != manifest["patchSha256"]:
        raise RuntimeError("The source or patch does not match the frozen manifest")
    with tempfile.TemporaryDirectory(prefix="omam-ship-native-") as temporary:
        root = Path(temporary)
        target = root / manifest["path"]
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        subprocess.run(["patch", "--batch", "--forward", "-p1"], input=patch, cwd=root, check=True, timeout=10, capture_output=True)
        applied = target.read_bytes()
        if digest(applied) != manifest["postSha256"]:
            raise RuntimeError("Applied source hash mismatch")
        text = applied.decode()
        start = text.index("/* OMAM ship-on-poweroff:")
        end = text.index("/* End OMAM ship-on-poweroff. */", start)
        hooks = text[start:end]
        harness = (here / "native_harness.c").read_text().replace("/* INSERT_PRODUCTION_HOOKS */", hooks)
        test_source = root / "native.c"
        test_source.write_text(harness)
        executable = root / "native"
        command = ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter", str(test_source), "-o", str(executable)]
        compiled = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        if compiled.returncode:
            print(compiled.stderr)
            raise RuntimeError("Native production-hook compilation failed")
        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10, check=False)
        if result.returncode:
            print(result.stderr)
            raise RuntimeError("Native production-hook assertion failed")
        report = json.loads(result.stdout)
        report.update({"exitCode": result.returncode, "preSha256": manifest["preSha256"], "postSha256": manifest["postSha256"],
                       "patchSha256": manifest["patchSha256"], "harnessSha256": digest((here / "native_harness.c").read_bytes()),
                       "compiler": subprocess.run(["cc", "--version"], capture_output=True, text=True, check=True, timeout=5).stdout.splitlines()[0],
                       "scope": "patched driver hooks with injected kernel boundary; not full kernel build or hardware"})
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))


if __name__ == "__main__":
    main()
