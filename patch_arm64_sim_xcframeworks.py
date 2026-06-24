#!/usr/bin/env python3
"""Patch one or more .framework directories into arm64-simulator .xcframeworks.

Input is explicit: pass the framework paths you want to patch. The script keeps the
original arm64 slice for device, patches a copied arm64 slice to iOS Simulator,
combines it with the original x86_64 simulator slice, and emits an xcframework.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

DEFAULT_POD_ROOT = Path(__file__).resolve().parent
GUARD_HEADER = "MLKitReleaseBuildGuard.h"
GUARD_TOKEN = "MLKIT_6A1F8D2C_NON_DEBUG_BUILD_FORBIDDEN"
GUARD_INCLUDE = '-include "${PODS_ROOT}/Headers/Public/GoogleMLKit/MLKitReleaseBuildGuard.h"'


def run(args: Iterable[object], *, cwd: Path | None = None, capture: bool = False) -> str:
    command = [str(arg) for arg in args]
    print("+ " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return result.stdout if capture and result.stdout is not None else ""


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write("\n")


def prompt_release_guard(mode: str) -> bool:
    if mode == "yes":
        return True
    if mode == "no":
        return False
    if not sys.stdin.isatty():
        raise SystemExit("--release-guard must be yes or no when stdin is not interactive")

    answer = input("Add non-DEBUG #error guard to podspecs? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def sdk_version() -> str:
    return run(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"], capture=True).strip()


def framework_binary(framework: Path) -> tuple[str, Path]:
    if framework.suffix != ".framework" or not framework.is_dir():
        raise RuntimeError(f"Not a .framework directory: {framework}")

    binary_name = framework.stem
    binary = framework / binary_name
    if not binary.exists():
        raise RuntimeError(f"Framework binary not found: {binary}")
    return binary_name, binary


def lipo_info(binary: Path) -> str:
    return run(["lipo", "-info", binary], capture=True)


def ensure_arches(binary: Path, framework: Path) -> None:
    info = lipo_info(binary)
    if "arm64" not in info or "x86_64" not in info:
        raise RuntimeError(f"{framework} must contain arm64 and x86_64 slices: {info.strip()}")


def sanitized_member_name(name: str, index: int) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    return f"{index:05d}_{safe or 'member.o'}"


def extract_archive_members(archive: Path, output_dir: Path) -> list[tuple[str, Path]]:
    data = archive.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise RuntimeError(f"Not an ar archive: {archive}")

    members: list[tuple[str, Path]] = []
    offset = 8
    index = 0
    while offset + 60 <= len(data):
        header = data[offset:offset + 60]
        if header[58:60] != b"`\n":
            raise RuntimeError(f"Invalid ar member header in {archive} at offset {offset}")

        raw_name = header[:16].decode("utf-8", errors="replace").strip()
        try:
            size = int(header[48:58].decode("ascii").strip())
        except ValueError as error:
            raise RuntimeError(f"Invalid ar member size in {archive} at offset {offset}") from error

        content_start = offset + 60
        content_end = content_start + size
        content = data[content_start:content_end]

        if raw_name.startswith("#1/"):
            name_length = int(raw_name[3:])
            name = content[:name_length].decode("utf-8", errors="replace").rstrip("\0")
            member_data = content[name_length:]
        else:
            name = raw_name.rstrip("/")
            member_data = content

        if not name.startswith("__.SYMDEF"):
            member_path = output_dir / sanitized_member_name(name, index)
            member_path.write_bytes(member_data)
            members.append((name, member_path))

        offset = content_end + (size % 2)
        index += 1

    return members


def patch_lc_build_version_platform(source: Path, output: Path) -> bool:
    data = bytearray(source.read_bytes())
    if len(data) < 32 or data[:4] != b"\xcf\xfa\xed\xfe":
        return False

    ncmds = int.from_bytes(data[16:20], "little")
    offset = 32
    patched = False
    for _ in range(ncmds):
        if offset + 8 > len(data):
            return False
        cmd = int.from_bytes(data[offset:offset + 4], "little")
        cmdsize = int.from_bytes(data[offset + 4:offset + 8], "little")
        if cmdsize < 8 or offset + cmdsize > len(data):
            return False

        if cmd == 0x32:  # LC_BUILD_VERSION
            platform_offset = offset + 8
            platform = int.from_bytes(data[platform_offset:platform_offset + 4], "little")
            if platform == 2:  # PLATFORM_IOS
                data[platform_offset:platform_offset + 4] = (7).to_bytes(4, "little")  # PLATFORM_IOSSIMULATOR
                patched = True
            elif platform == 7:
                patched = True
        offset += cmdsize

    if not patched:
        return False

    output.write_bytes(data)
    return True


def patch_macho_object(source: Path, output: Path, min_ios: str, sim_sdk: str) -> None:
    if patch_lc_build_version_platform(source, output):
        return

    run([
        "xcrun",
        "vtool",
        "-set-build-version",
        "iossim",
        min_ios,
        sim_sdk,
        "-replace",
        "-output",
        output,
        source,
    ])


def patch_arm64_archive(source_archive: Path, output_archive: Path, min_ios: str, sim_sdk: str) -> None:
    with tempfile.TemporaryDirectory(prefix="arm64-sim-objects-") as temp_dir:
        object_dir = Path(temp_dir)
        members = extract_archive_members(source_archive, object_dir)

        patched_members: list[Path] = []
        for _member_name, member_path in members:
            file_type = run(["file", member_path], capture=True)
            if "Mach-O" not in file_type:
                continue

            patched_path = member_path.with_suffix(member_path.suffix + ".patched")
            patch_macho_object(member_path, patched_path, min_ios, sim_sdk)
            member_path.unlink()
            patched_path.rename(member_path)
            patched_members.append(member_path)

        if not patched_members:
            raise RuntimeError(f"No Mach-O object files found in {source_archive}")

        output_archive.parent.mkdir(parents=True, exist_ok=True)
        if output_archive.exists():
            output_archive.unlink()
        run(["xcrun", "libtool", "-static", "-o", output_archive, *patched_members])


def copy_framework_with_binary(source_framework: Path, destination: Path, binary_name: str, binary: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source_framework, destination)
    shutil.copy2(binary, destination / binary_name)


def output_path_for(framework: Path, output: Path | None, output_dir: Path | None) -> Path:
    if output and output_dir:
        raise RuntimeError("Use only one of --output or --output-dir")
    if output:
        return output.resolve()
    if output_dir:
        return (output_dir.resolve() / f"{framework.stem}.xcframework")
    return framework.with_suffix(".xcframework").resolve()


def create_xcframework(
    *,
    framework: Path,
    output_xcframework: Path,
    min_ios: str,
    sim_sdk: str,
    build_root: Path,
) -> None:
    binary_name, source_binary = framework_binary(framework)
    ensure_arches(source_binary, framework)

    work_dir = build_root / binary_name
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    device_arm64 = work_dir / f"{binary_name}-ios-arm64.a"
    sim_x86_64 = work_dir / f"{binary_name}-iossim-x86_64.a"
    sim_arm64 = work_dir / f"{binary_name}-iossim-arm64.a"
    sim_universal = work_dir / f"{binary_name}-iossim-universal.a"

    run(["lipo", source_binary, "-thin", "arm64", "-output", device_arm64])
    run(["lipo", source_binary, "-thin", "x86_64", "-output", sim_x86_64])
    patch_arm64_archive(device_arm64, sim_arm64, min_ios, sim_sdk)
    run(["lipo", "-create", sim_x86_64, sim_arm64, "-output", sim_universal])

    device_framework = work_dir / "device" / framework.name
    simulator_framework = work_dir / "simulator" / framework.name
    copy_framework_with_binary(framework, device_framework, binary_name, device_arm64)
    copy_framework_with_binary(framework, simulator_framework, binary_name, sim_universal)

    if output_xcframework.exists():
        shutil.rmtree(output_xcframework)
    output_xcframework.parent.mkdir(parents=True, exist_ok=True)

    run([
        "xcodebuild",
        "-create-xcframework",
        "-framework",
        device_framework,
        "-framework",
        simulator_framework,
        "-output",
        output_xcframework,
    ])


def add_guard_to_xcconfig(spec: dict) -> None:
    config = spec.setdefault("user_target_xcconfig", {})
    flags = config.get("OTHER_CFLAGS", "$(inherited)")
    if GUARD_INCLUDE not in flags:
        config["OTHER_CFLAGS"] = f"{flags} {GUARD_INCLUDE}".strip()


def remove_guard_from_xcconfig(spec: dict) -> None:
    config = spec.get("user_target_xcconfig")
    if not isinstance(config, dict):
        return
    flags = config.get("OTHER_CFLAGS")
    if not isinstance(flags, str):
        return

    parts = flags.split()
    cleaned: list[str] = []
    index = 0
    guard_header = '"${PODS_ROOT}/Headers/Public/GoogleMLKit/MLKitReleaseBuildGuard.h"'
    while index < len(parts):
        if index + 1 < len(parts) and parts[index] == "-include" and parts[index + 1] == guard_header:
            index += 2
            continue
        cleaned.append(parts[index])
        index += 1

    if cleaned:
        config["OTHER_CFLAGS"] = " ".join(cleaned)
    else:
        config.pop("OTHER_CFLAGS", None)


def set_vendored_xcframework(spec: dict, pod_root: Path, output_xcframework: Path) -> None:
    try:
        relative = output_xcframework.resolve().relative_to(pod_root.resolve())
    except ValueError:
        return
    spec["vendored_frameworks"] = [str(relative)]


def update_podspec_for_output(pod_root: Path, output_xcframework: Path) -> None:
    name = output_xcframework.stem
    podspec = pod_root / f"{name}.podspec.json"
    if not podspec.exists():
        return
    spec = load_json(podspec)
    set_vendored_xcframework(spec, pod_root, output_xcframework)
    write_json(podspec, spec)


def apply_release_guard(pod_root: Path, enabled: bool) -> None:
    guard_path = pod_root / GUARD_HEADER
    if enabled:
        guard_path.write_text(
            "#ifndef MLKIT_RELEASE_BUILD_GUARD_H\n"
            "#define MLKIT_RELEASE_BUILD_GUARD_H\n\n"
            "#if !defined(DEBUG)\n"
            f"#error \"Patched framework is only allowed in DEBUG builds: {GUARD_TOKEN}\"\n"
            "#endif\n\n"
            "#endif /* MLKIT_RELEASE_BUILD_GUARD_H */\n",
            encoding="utf-8",
        )

    for podspec in sorted(pod_root.glob("*.podspec.json")):
        spec = load_json(podspec)
        if enabled:
            add_guard_to_xcconfig(spec)
        else:
            remove_guard_from_xcconfig(spec)

        if spec.get("name") == "GoogleMLKit":
            for subspec in spec.get("subspecs", []):
                if subspec.get("name") != "MLKitCore":
                    continue
                for key in ("preserve_paths", "source_files"):
                    values = subspec.setdefault(key, [])
                    if enabled and GUARD_HEADER not in values:
                        values.append(GUARD_HEADER)
                    if not enabled:
                        subspec[key] = [value for value in values if value != GUARD_HEADER]

        write_json(podspec, spec)


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch explicit .framework paths into arm64-simulator xcframeworks.")
    parser.add_argument("frameworks", nargs="+", type=Path, help="Input .framework path(s) to patch")
    parser.add_argument("--output", type=Path, help="Output .xcframework path; only valid with one input framework")
    parser.add_argument("--output-dir", type=Path, help="Directory for generated .xcframeworks")
    parser.add_argument("--min-ios", default="15.5", help="Minimum iOS version written into patched simulator objects")
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_POD_ROOT / ".build" / "arm64-sim-patch")
    parser.add_argument("--pod-root", type=Path, default=None, help="Podspec root to update for vendored_frameworks and release guard")
    parser.add_argument("--no-podspec-update", action="store_true", help="Do not update podspec vendored_frameworks")
    parser.add_argument(
        "--release-guard",
        choices=("ask", "yes", "no"),
        default="ask",
        help="Whether podspecs should inject a non-DEBUG #error guard header",
    )
    args = parser.parse_args()

    if args.output and len(args.frameworks) != 1:
        raise SystemExit("--output can only be used with one framework input")

    pod_root = args.pod_root.resolve() if args.pod_root else None
    build_root = args.build_dir.resolve()
    sim_sdk = sdk_version()
    release_guard = prompt_release_guard(args.release_guard) if pod_root else None

    if release_guard is not None:
        print(f"Release guard: {'enabled' if release_guard else 'disabled'}")
    print(f"Simulator SDK: {sim_sdk}")

    generated_outputs: list[Path] = []
    for framework_arg in args.frameworks:
        framework = framework_arg.resolve()
        output = output_path_for(framework, args.output, args.output_dir)
        print(f"Patching {framework} -> {output}")
        create_xcframework(
            framework=framework,
            output_xcframework=output,
            min_ios=args.min_ios,
            sim_sdk=sim_sdk,
            build_root=build_root,
        )
        generated_outputs.append(output)

    if pod_root:
        if not args.no_podspec_update:
            for output in generated_outputs:
                update_podspec_for_output(pod_root, output)
        apply_release_guard(pod_root, bool(release_guard))

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
