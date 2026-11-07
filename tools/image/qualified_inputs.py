"""Exact TCH REL3.3 vendor, kernel and .NET input validation, shared with private consumers."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
from image_io import digest, write_json
from sdk_payload import tree_sha256, validate_manifest

REL33_ARCHIVE_SHA256 = "a1ccde9b961bfbfe1a7e4efa8da75eab6342d7b8b0723e9dd28d6032a5893d43"


REL33_ROOTFS_SHA256 = "4812a431aa677c6cdafe47a1e429902ecd15b8200ab47dd05bf4d23334c16d98"


VENDOR_FILES = ("Firmware/Image_uuu", "Firmware/flash_uuu.bin", "Firmware/imx91-iwg50m-prgjj_uuu.dtb",
                "Firmware/initramfs.cpio.zst.u-boot", "Linux/Image", "Linux/flash.bin",
                "Linux/imx91-iwg62h-prgtt.dtb", "Linux/rootfs.tar.zst", "iG-PRGTT-TelematicsConnectHub.uuu")


KERNEL_OUTPUTS = ("Image", "imx91-iwg62h-prgtt.dtb", "config", "Module.symvers", "System.map", "module-symbols.symvers")


QUALIFIED = "PASS_BUILD_AND_STATIC_ABI"


STOCK_MODULES = {"cryptodev.ko", "igb_uio.ko", "kpage_ncache.ko", "mlan.ko", "moal.ko"}


IMU_MODULES = {"st_lsm6dsx.ko", "st_lsm6dsx_i2c.ko"}


SOURCE_IDENTITIES = {"srcrev", "energy_patch_sha256", "energy_source_manifest_sha256", "energy_driver_sha256",
                     "flexcan_manifest_sha256", "uapi_sha256"}


def document(path, label):
    """Read a bounded strict object without following a final symlink."""
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 8 * 1024**2:
        raise ValueError(label + " must be a bounded regular JSON file")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(label + " contains duplicate properties")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError(label + " contains a non-JSON number")

    result = json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(result, dict):
        raise ValueError(label + " must be a JSON object")
    return result


def relative_name(value):
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
            or PurePosixPath(value).is_absolute() or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("Qualified input has an unsafe relative path")
    return value


def checked_file(root, name, record):
    """Bind a regular file's size and bytes to a declaration; refuse link traversal."""
    root, name = Path(root), relative_name(name)
    if (not isinstance(record, dict) or type(record.get("bytes")) is not int or record["bytes"] < 0
            or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
        raise ValueError("Qualified input has an invalid file declaration: " + name)
    path = root
    for part in Path(name).parts:
        if path.is_symlink():
            raise ValueError("Qualified input traverses a symlink: " + name)
        path /= part
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size != record["bytes"] or digest(path) != record["sha256"]):
        raise ValueError("Qualified input bytes differ: " + name)
    return path


def regular_files(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Qualified tree must be an ordinary directory")
    result = set()
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            result.add(path.relative_to(root).as_posix())
        elif not stat.S_ISDIR(mode):
            raise ValueError("Qualified tree contains a link or special file: " + path.relative_to(root).as_posix())
    return result


def validate_vendor(root, manifest_path, rootfs_pin=None):
    manifest = document(manifest_path, "REL3.3 preparation manifest")
    if (manifest.get("schema") != "omam.rel33.preparation-inputs.v1" or manifest.get("status") != "ready"
            or manifest.get("archive_sha256") != REL33_ARCHIVE_SHA256):
        raise ValueError("REL3.3 requires its completed preparation manifest and original archive identity")
    declared = manifest.get("files")
    if not isinstance(declared, dict):
        raise ValueError("REL3.3 preparation manifest has no files")
    files = {}
    for name in VENDOR_FILES:
        item = declared.get("inputs/vendor/" + name)
        if (not isinstance(item, dict)
                or item.get("member") != "iG-PRGTT-BN-R2.0-REL3.3_Telematics_Connect_Hub/" + name):
            raise ValueError("REL3.3 preparation manifest does not identify vendor file: " + name)
        checked_file(root, name, item)
        files[name] = {key: item[key] for key in ("bytes", "sha256")}
    if files["Linux/rootfs.tar.zst"]["sha256"] != (rootfs_pin or REL33_ROOTFS_SHA256):
        raise ValueError("REL3.3 cannot relabel a different stock rootfs")
    return {"vendor_release": "TCH REL3.3", "archive_sha256": REL33_ARCHIVE_SHA256,
            "manifest_sha256": digest(manifest_path), "files": files}


def release_sources(energy_manifest, energy_patch, flexcan_manifest, uapi):
    """The final release requires both controller filtering and the reviewed BQ25601 charger patch."""
    energy = document(energy_manifest, "Energy driver source manifest")
    if (energy.get("schemaVersion") != 1 or energy.get("path") != "drivers/power/supply/bq256xx_charger.c"
            or not isinstance(energy.get("upstreamRevision"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", energy["upstreamRevision"])
            or any(not isinstance(energy.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", energy[key])
                   for key in ("patchSha256", "postSha256"))):
        raise ValueError("Energy source manifest does not identify the required BQ25601 driver patch")
    energy_patch = Path(energy_patch)
    checked_file(energy_patch.parent, energy_patch.name,
                 {"bytes": energy_patch.stat().st_size, "sha256": energy["patchSha256"]})
    for path in (Path(flexcan_manifest), Path(uapi)):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Release source identities must be regular files")
    return {"srcrev": energy["upstreamRevision"], "energy_patch_sha256": energy["patchSha256"],
            "energy_source_manifest_sha256": digest(energy_manifest), "energy_driver_sha256": energy["postSha256"],
            "flexcan_manifest_sha256": digest(flexcan_manifest), "uapi_sha256": digest(uapi)}


def validate_kernel(root, receipt_path, vendor, sources):
    root = Path(root)
    receipt = document(receipt_path, "Kernel build receipt")
    if receipt.get("schema") != "omam.rel33.kernel-build.v2" or receipt.get("status") != QUALIFIED:
        raise ValueError("REL3.3 requires a completed kernel build and static ABI qualification")
    qualification = receipt.get("qualification", {})
    if not isinstance(qualification, dict):
        raise ValueError("Kernel qualification must be an object")
    manifest_path = root / "kernel-manifest.json"
    manifest = document(manifest_path, "Kernel set manifest")
    if (qualification.get("status") != QUALIFIED
            or qualification.get("kernel_manifest", {}).get("sha256") != digest(manifest_path)
            or manifest.get("schema") != "omam.kernel-set.v1" or manifest.get("static_qualification") != QUALIFIED):
        raise ValueError("Kernel manifest differs from the finished qualification receipt")
    release = manifest.get("release")
    inputs = manifest.get("inputs", {})
    if (not isinstance(release, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", release)
            or not isinstance(inputs, dict) or inputs != receipt.get("inputs")
            or inputs.get("kernel_release") != release
            or inputs.get("vendor_receipt_sha256") != vendor["manifest_sha256"]):
        raise ValueError("Kernel source/release identity differs from its vendor or qualification receipt")
    if (not isinstance(sources, dict) or set(sources) != SOURCE_IDENTITIES
            or any(inputs.get(key) != value for key, value in sources.items())):
        raise ValueError("Kernel does not match the required BQ25601 and FlexCAN release source identities")
    outputs = manifest.get("outputs")
    if (not isinstance(outputs, dict) or set(outputs) != set(KERNEL_OUTPUTS)
            or outputs != qualification.get("outputs")
            or outputs.get("config", {}).get("sha256") != receipt.get("config_sha256")):
        raise ValueError("Kernel output identity is incomplete or inconsistent")
    modules, metadata = manifest.get("modules"), manifest.get("module_metadata")
    if not isinstance(modules, dict) or not isinstance(metadata, dict) or not metadata:
        raise ValueError("Kernel manifest must qualify modules and all depmod metadata")
    prefix = "lib/modules/" + release + "/"
    if (any(not isinstance(name, str) or not name.startswith(prefix) or not name.endswith(".ko") for name in modules)
            or any(not isinstance(name, str) or not name.startswith(prefix) or name.endswith(".ko") for name in metadata)
            or modules.keys() & metadata.keys()):
        raise ValueError("Kernel manifest mixes releases or module declarations")
    stock = manifest.get("stock_external_provenance", {})
    stock_files = stock.get("files", {})
    imu = {name for name in modules if name.startswith(prefix + "updates/omam/")}
    native = {name for name in modules if name.startswith(prefix + "kernel/")}
    expected_counts = {"native_modules": 807, "stock_modules": 5, "imu_modules": 2, "total_modules": 814}
    if (any(type(qualification.get(key)) is not int or qualification[key] != count for key, count in expected_counts.items())
            or len(native) != 807 or len(imu) != 2 or len(modules) != 814
            or not isinstance(stock_files, dict) or len(stock_files) != 5
            or {Path(name).name for name in stock_files} != STOCK_MODULES
            or {Path(name).name for name in imu} != IMU_MODULES
            or set(modules) != native | imu | set(stock_files)
            or stock.get("rootfs_sha256") != vendor["files"]["Linux/rootfs.tar.zst"]["sha256"]):
        raise ValueError("Kernel set must contain all 807 native, five preserved stock and two qualified IMU modules")
    for name, original in stock_files.items():
        if (not isinstance(original, dict) or not isinstance(modules[name], dict)
                or original.get("sha256") != modules[name].get("sha256")):
            raise ValueError("Preserved stock module differs from its provenance")
    required_metadata = {prefix + name for name in ("modules.dep", "modules.dep.bin", "modules.alias", "modules.alias.bin",
                                                    "modules.symbols", "modules.symbols.bin", "modules.builtin",
                                                    "modules.builtin.modinfo", "modules.order", "updates/omam/manifest.json")}
    if not required_metadata <= metadata.keys():
        raise ValueError("Kernel set is missing required module indexes or its IMU manifest")
    expected = {"kernel-manifest.json", *outputs, *modules, *metadata}
    if regular_files(root) != expected:
        raise ValueError("Kernel set has missing or unqualified files")
    for table in (outputs, modules, metadata):
        for name, record in table.items():
            checked_file(root, name, record)
    return {"root": root, "manifest": manifest, "manifest_sha256": digest(manifest_path),
            "receipt_sha256": digest(receipt_path), "counts": expected_counts}


def validate_sdk(sdk, lock_path, global_path, publications):
    """Use the locked SDK and its own runtimeconfig, never derive runtime versions from SDK strings."""
    sdk = Path(sdk)
    lock = document(lock_path, "SDK lock")
    pins = validate_manifest(lock)
    report = document(sdk / "sdk-payload-report.json", "SDK payload report")
    validate_manifest(report)
    declared = report["sdks"]
    fields = ("version", "rid", "url", "sha512", "metadata_url")
    if (len(declared) != len(pins)
            or any(any(left.get(key) != right.get(key) for key in fields) for left, right in zip(declared, pins))
            or report.get("host_from_sdk") != pins[-1]["version"]):
        raise ValueError("SDK payload differs from the current locked SDK")
    global_sdk = document(global_path, "Global SDK selection").get("sdk", {})
    version = pins[-1]["version"]
    if (not version.startswith("11.") or global_sdk.get("version") != version or global_sdk.get("rollForward") != "disable"):
        raise ValueError("Image SDK must match the exact current .NET 11 build SDK")
    if not isinstance(report.get("tree_sha256"), str) or tree_sha256(sdk) != report["tree_sha256"]:
        raise ValueError("SDK payload bytes differ from their staging report")
    config = document(sdk / "sdk" / version / "dotnet.runtimeconfig.json", "SDK runtimeconfig")
    framework = config.get("runtimeOptions", {}).get("framework", {})
    runtime = framework.get("version")
    if framework.get("name") != "Microsoft.NETCore.App" or not isinstance(runtime, str) or not runtime.startswith("11."):
        raise ValueError("SDK runtimeconfig does not identify its .NET 11 runtime")
    shared = {}
    for name in ("Microsoft.NETCore.App", "Microsoft.AspNetCore.App"):
        directory = sdk / "shared" / name
        versions = sorted(path.name for path in directory.iterdir() if path.is_dir() and not path.is_symlink())
        if len(versions) != 1 or not versions[0].startswith("11."):
            raise ValueError("Image SDK must contain one current runtime for " + name)
        shared[name] = versions[0]
    if shared["Microsoft.NETCore.App"] != runtime:
        raise ValueError("SDK runtimeconfig and shared runtime disagree")
    for role, manifest in publications.items():
        published_sdk, published_runtime = manifest.get("sdk", {}), manifest.get("runtime", {})
        if (manifest.get("rid") != "linux-arm64" or published_sdk.get("pinned") != version
                or published_sdk.get("used") != version or published_sdk.get("matchesPin") is not True
                or published_sdk.get("rollForward") != "disable" or published_runtime.get("version") != runtime
                or published_runtime.get("tfm") != "net11.0" or published_runtime.get("selfContained") is not True):
            raise ValueError("Published " + role + " does not use the current locked SDK/runtime")
        frameworks = published_runtime.get("includedFrameworks")
        if (not isinstance(frameworks, list) or not frameworks
                or any(not isinstance(item, dict) for item in frameworks)
                or not any(item.get("name") == "Microsoft.NETCore.App" for item in frameworks)
                or any(shared.get(item.get("name")) != item.get("version") for item in frameworks)):
            raise ValueError("Published " + role + " contains a mismatched shared runtime")
        expected = {name + ".Runtime.linux-arm64": value for name, value in shared.items()}
        for pack in manifest.get("runtimePacks", []):
            if not isinstance(pack, dict) or expected.get(pack.get("id")) != pack.get("version"):
                raise ValueError("Published " + role + " contains a mismatched runtime pack")
    return {"sdk_versions": [pin["version"] for pin in pins], "host_version": version,
            "runtime_versions": shared, "tree_sha256": report["tree_sha256"],
            "lock_sha256": digest(lock_path), "payload_report_sha256": digest(sdk / "sdk-payload-report.json")}


def copy_bound_file(source_root, relative, record, target):
    source = checked_file(source_root, relative, record)
    target = Path(target)
    if any(parent.is_symlink() for parent in target.parents):
        raise ValueError("Qualified staging cannot traverse a symlink")
    missing = []
    parent = target.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)
    if target.exists() or target.is_symlink():
        raise ValueError("Qualified staging must not replace an existing file: " + target.name)
    with source.open("rb") as stream, target.open("xb") as destination:
        shutil.copyfileobj(stream, destination, 1024 * 1024)
    target.chmod(0o644)
    if digest(target) != record["sha256"]:
        raise ValueError("Qualified input changed during staging: " + relative)


def stage_kernel(kernel, overlay, vendor_types, metadata_prefix="usr/share/iwave-linux-extensions/kernel"):
    """Stage one qualified release in Yocto's real usr/lib path and explicitly remove old module trees."""
    overlay = Path(overlay)
    manifest, root = kernel["manifest"], kernel["root"]
    if vendor_types.get("usr/lib/modules") != tarfile.DIRTYPE:
        raise ValueError("REL3.3 rootfs must provide the expected usr/lib/modules directory")
    deletes = {name for name in vendor_types if name.startswith("usr/lib/modules/") or name.startswith("lib/modules/")}
    if "lib/modules" in vendor_types:
        deletes.add("lib/modules")
    for table in (manifest["modules"], manifest["module_metadata"]):
        for name, record in table.items():
            copy_bound_file(root, name, record, overlay / ("usr/" + name))
    metadata = overlay / metadata_prefix / manifest["release"]
    for name in ("config", "Module.symvers", "System.map", "module-symbols.symvers"):
        copy_bound_file(root, name, manifest["outputs"][name], metadata / name)
    copy_bound_file(root, "kernel-manifest.json",
                    {"bytes": (root / "kernel-manifest.json").stat().st_size, "sha256": kernel["manifest_sha256"]},
                    metadata / "kernel-manifest.json")
    write_json(metadata / "qualification.json", {"static_qualification": QUALIFIED,
               "kernel_manifest_sha256": kernel["manifest_sha256"], "build_receipt_sha256": kernel["receipt_sha256"],
               "physical_loading": "NOT_RUN", "hardware_readback": "NOT_RUN"}, 0o644)
    return deletes


def verify_staged_kernel(kernel, overlay):
    """No installer may reintroduce another kernel or mutate qualified indexes/modules."""
    manifest, overlay = kernel["manifest"], Path(overlay)
    expected = {**manifest["modules"], **manifest["module_metadata"]}
    prefix = "lib/modules/"
    actual = {prefix + name for name in regular_files(overlay / "usr/lib/modules")}
    if actual != set(expected) or (overlay / "lib/modules").exists() or (overlay / "lib/modules").is_symlink():
        raise ValueError("Offline installer changed the qualified module set or introduced another release")
    for name, record in expected.items():
        checked_file(overlay, "usr/" + name, record)


def copy_boot_inputs(vendor_root, vendor, kernel, output):
    """The factory flasher uses stock recovery firmware and bootloader, but the qualified Image/DTB."""
    for name in VENDOR_FILES:
        if name == "Linux/rootfs.tar.zst":
            continue
        kernel_name = name.removeprefix("Linux/")
        if name in ("Linux/Image", "Linux/imx91-iwg62h-prgtt.dtb"):
            copy_bound_file(kernel["root"], kernel_name, kernel["manifest"]["outputs"][kernel_name], Path(output) / name)
        else:
            copy_bound_file(vendor_root, name, vendor["files"][name], Path(output) / name)
