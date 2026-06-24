#!/usr/bin/env python3
"""Generate local patched pods from official podspec.json source tarballs.

The selected config file controls the output directory and MLKit versions. The
script materializes all configured pod payloads from Google's official tarballs,
patches selected frameworks into xcframeworks, updates podspec.json files, and can
inject a non-DEBUG build guard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
PATCH_SCRIPT = ROOT / "patch_arm64_sim_xcframeworks.py"
DEFAULT_PODS = ("MLImage", "MLKitCommon", "MLKitVision")
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


def curl_args(url: str) -> list[str]:
    return [
        "curl",
        "-L",
        "--fail",
        "--retry",
        "3",
        "--retry-all-errors",
        "--retry-delay",
        "1",
        "--connect-timeout",
        "30",
        "--silent",
        "--show-error",
        url,
    ]


def fetch_text(url: str) -> str:
    return run(curl_args(url), capture=True)


def fetch_file(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run([*curl_args(url), "-o", destination])


def prompt_release_guard(mode: str) -> bool:
    if mode == "yes":
        return True
    if mode == "no":
        return False
    if not sys.stdin.isatty():
        raise SystemExit("--release-guard must be yes or no when stdin is not interactive")

    answer = input("Add non-DEBUG #error guard to podspecs? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        raise RuntimeError(f"Config file not found: {config_path}")
    config = load_json(config_path)
    if not isinstance(config.get("pods"), dict):
        raise RuntimeError(f"Config file must contain a pods object: {config_path}")
    if not config.get("output_dir"):
        raise RuntimeError(f"Config file must contain output_dir: {config_path}")
    return config


def configured_default_pods(config: dict) -> tuple[str, ...]:
    pods = config.get("default_patch_pods")
    if isinstance(pods, list) and pods:
        return tuple(str(pod) for pod in pods)
    return DEFAULT_PODS


def configured_output_root(base_root: Path, config: dict) -> Path:
    value = Path(str(config["output_dir"]))
    return value.resolve() if value.is_absolute() else (base_root / value).resolve()


def version_for(pods_config: dict, pod_name: str) -> str | None:
    pod_config = pods_config.get(pod_name)
    if not isinstance(pod_config, dict):
        return None
    version = pod_config.get("version")
    return str(version) if version else None


def major_minor(version: str) -> str:
    parts = version.split("-", 1)[0].split(".")
    if len(parts) < 2:
        return version
    return ".".join(parts[:2])


def major_minor_patch(version: str) -> str:
    parts = version.split("-", 1)[0].split(".")
    if len(parts) < 3:
        return version
    return ".".join(parts[:3])


def pessimistic_minor(pods_config: dict, pod_name: str) -> str | None:
    version = version_for(pods_config, pod_name)
    return f"~> {major_minor(version)}" if version else None


def pessimistic_patch(pods_config: dict, pod_name: str) -> str | None:
    version = version_for(pods_config, pod_name)
    return f"~> {major_minor_patch(version)}" if version else None


def exact_with_official_space(pods_config: dict, pod_name: str) -> str | None:
    version = version_for(pods_config, pod_name)
    return f" {version}" if version else None


def set_dependency(dependencies: dict, pod_name: str, requirement: str | None) -> None:
    if requirement and pod_name in dependencies:
        dependencies[pod_name] = requirement


def apply_known_dependency_versions(spec: dict, pods_config: dict) -> None:
    name = spec.get("name")

    if name == "MLKitVision":
        dependencies = spec.get("dependencies", {})
        set_dependency(dependencies, "MLImage", exact_with_official_space(pods_config, "MLImage"))
        set_dependency(dependencies, "MLKitCommon", pessimistic_minor(pods_config, "MLKitCommon"))
        return

    if name == "MLKitBarcodeScanning":
        dependencies = spec.get("dependencies", {})
        set_dependency(dependencies, "MLKitCommon", pessimistic_minor(pods_config, "MLKitCommon"))
        set_dependency(dependencies, "MLKitVision", pessimistic_minor(pods_config, "MLKitVision"))
        return

    if name != "GoogleMLKit":
        return

    for subspec in spec.get("subspecs", []):
        dependencies = subspec.get("dependencies", {})
        set_dependency(dependencies, "MLKitCommon", pessimistic_patch(pods_config, "MLKitCommon"))
        set_dependency(dependencies, "MLKitBarcodeScanning", pessimistic_patch(pods_config, "MLKitBarcodeScanning"))
        set_dependency(dependencies, "MLKitVision", pessimistic_patch(pods_config, "MLKitVision"))


def pod_config_for(pods_config: dict, pod_name: str) -> dict:
    pod_config = pods_config.get(pod_name)
    if not isinstance(pod_config, dict):
        raise RuntimeError(f"Missing pod config for {pod_name}")
    return pod_config


def version_required(pods_config: dict, pod_name: str) -> str:
    version = version_for(pods_config, pod_name)
    if not version:
        raise RuntimeError(f"Missing version for {pod_name}")
    return version


def specs_repo_raw_url(pod_name: str, version: str) -> str:
    digest = hashlib.md5(pod_name.encode("utf-8")).hexdigest()
    shard = "/".join(digest[:3])
    return (
        "https://raw.githubusercontent.com/CocoaPods/Specs/master/"
        f"Specs/{shard}/{pod_name}/{version}/{pod_name}.podspec.json"
    )


def official_podspec(pod_name: str, version: str) -> dict:
    url = specs_repo_raw_url(pod_name, version)
    print(f"Downloading official podspec {url}")
    return json.loads(fetch_text(url))


def remove_excluded_archs(value: object) -> None:
    if isinstance(value, dict):
        for key in list(value.keys()):
            if isinstance(key, str) and key.startswith("EXCLUDED_ARCHS"):
                value.pop(key, None)
                continue
            remove_excluded_archs(value[key])
    elif isinstance(value, list):
        for item in value:
            remove_excluded_archs(item)


def prefixed_pod_path(value: str, pod_name: str) -> str:
    if value.startswith(("/", "$(inherited)", "${", "$(")) or value.startswith(f"{pod_name}/"):
        return value
    return f"{pod_name}/{value}"


def prefix_pod_paths(value: object, pod_name: str) -> object:
    if isinstance(value, str):
        return prefixed_pod_path(value, pod_name)
    if isinstance(value, list):
        return [prefix_pod_paths(item, pod_name) for item in value]
    return value


def apply_repo_layout_paths(spec: dict, pod_name: str) -> None:
    if pod_name == "GoogleMLKit":
        return
    for key in ("vendored_frameworks", "preserve_paths", "source_files", "resources"):
        if key in spec:
            spec[key] = prefix_pod_paths(spec[key], pod_name)


def apply_config_overrides(spec: dict, pod_name: str, pods_config: dict) -> dict:
    updated = dict(spec)
    updated["version"] = version_required(pods_config, pod_name)
    remove_excluded_archs(updated)
    apply_repo_layout_paths(updated, pod_name)
    return updated


def write_official_podspecs(output_root: Path, pods_config: dict) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for pod_name in pods_config:
        version = version_required(pods_config, pod_name)
        spec = official_podspec(pod_name, version)
        spec = apply_config_overrides(spec, pod_name, pods_config)
        write_json(output_root / f"{pod_name}.podspec.json", spec)


def sync_configured_podspecs(output_root: Path, pods_config: dict) -> None:
    for pod_name in pods_config:
        path = output_root / f"{pod_name}.podspec.json"
        if not path.exists():
            raise RuntimeError(f"Missing output podspec: {path}")
        spec = apply_config_overrides(load_json(path), pod_name, pods_config)
        write_json(path, spec)


def download(url: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / url.rsplit("/", 1)[-1]

    print(f"Downloading {url}")
    fetch_file(url, archive)
    return archive


def safe_extract_tar(archive: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    destination_resolved = destination.resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination_resolved) + "/") and target != destination_resolved:
                raise RuntimeError(f"Unsafe tar member path: {member.name}")
        tar.extractall(destination)


def podspec_path(root: Path, pod_name: str) -> Path:
    path = root / f"{pod_name}.podspec.json"
    if not path.exists():
        raise RuntimeError(f"Missing podspec: {path}")
    return path


def source_url(spec: dict, pod_name: str) -> str:
    url = spec.get("source", {}).get("http")
    if not url:
        raise RuntimeError(f"{pod_name}.podspec.json has no source.http")
    return url


def binary_name(spec: dict, pod_name: str) -> str:
    return str(spec.get("module_name") or spec.get("name") or pod_name)


def find_pod_payload(extracted_root: Path, pod_name: str) -> Path:
    direct = extracted_root / pod_name
    if direct.is_dir():
        return direct

    if (extracted_root / "Frameworks").is_dir() or (extracted_root / "MLKitCore").is_dir():
        return extracted_root

    matches = [path for path in extracted_root.rglob(pod_name) if path.is_dir() and path.name == pod_name]
    if not matches:
        raise RuntimeError(f"Could not find official pod payload directory for {pod_name} in {extracted_root}")
    if len(matches) > 1:
        raise RuntimeError(f"Found multiple payload directories for {pod_name}: {matches}")
    return matches[0]


def find_framework(pod_payload: Path, name: str) -> Path:
    expected = pod_payload / "Frameworks" / f"{name}.framework"
    if expected.is_dir():
        return expected

    matches = [path for path in pod_payload.rglob(f"{name}.framework") if path.is_dir()]
    if not matches:
        raise RuntimeError(f"Could not find {name}.framework in {pod_payload}")
    if len(matches) > 1:
        raise RuntimeError(f"Found multiple {name}.framework directories: {matches}")
    return matches[0]


def copy_official_payload(output_root: Path, pod_name: str, payload: Path) -> Path:
    if pod_name == "GoogleMLKit":
        for child in payload.iterdir():
            destination = output_root / child.name
            if destination.is_dir():
                shutil.rmtree(destination)
            elif destination.exists():
                destination.unlink()
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)
        return output_root

    destination = output_root / pod_name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(payload, destination)
    return destination


def materialize_official_pod(output_root: Path, pod_name: str, build_dir: Path) -> Path:
    spec = load_json(podspec_path(output_root, pod_name))
    archive = download(source_url(spec, pod_name), build_dir / "downloads")
    extracted_root = build_dir / "extracted" / pod_name
    safe_extract_tar(archive, extracted_root)
    payload = find_pod_payload(extracted_root, pod_name)
    return copy_official_payload(output_root, pod_name, payload)


def set_vendored_xcframework(spec: dict, pod_name: str, name: str) -> None:
    spec["vendored_frameworks"] = [f"{pod_name}/Frameworks/{name}.xcframework"]


def remove_official_framework(output_root: Path, pod_name: str, name: str) -> None:
    framework = output_root / pod_name / "Frameworks" / f"{name}.framework"
    if framework.exists():
        shutil.rmtree(framework)


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


def apply_googlemlkit_guard_files(spec: dict, enabled: bool) -> None:
    if spec.get("name") != "GoogleMLKit":
        return

    for subspec in spec.get("subspecs", []):
        if subspec.get("name") != "MLKitCore":
            continue
        for key in ("preserve_paths", "source_files"):
            values = subspec.setdefault(key, [])
            if enabled and GUARD_HEADER not in values:
                values.append(GUARD_HEADER)
            if not enabled:
                subspec[key] = [value for value in values if value != GUARD_HEADER]


def apply_release_guard(output_root: Path, enabled: bool) -> None:
    guard_path = output_root / GUARD_HEADER
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

    for path in sorted(output_root.glob("*.podspec.json")):
        spec = load_json(path)
        if enabled:
            add_guard_to_xcconfig(spec)
        else:
            remove_guard_from_xcconfig(spec)
        apply_googlemlkit_guard_files(spec, enabled)
        write_json(path, spec)


def patch_framework(framework: Path, output: Path, build_dir: Path, min_ios: str) -> None:
    run([
        sys.executable,
        PATCH_SCRIPT,
        framework,
        "--output",
        output,
        "--build-dir",
        build_dir,
        "--min-ios",
        min_ios,
    ])


def patch_pod(output_root: Path, pod_name: str, build_dir: Path, min_ios_override: str | None) -> None:
    spec_path = podspec_path(output_root, pod_name)
    spec = load_json(spec_path)
    name = binary_name(spec, pod_name)
    min_ios = min_ios_override or str(spec.get("platforms", {}).get("ios", "15.5"))
    local_payload = output_root / pod_name
    local_framework = find_framework(local_payload, name)
    output = output_root / pod_name / "Frameworks" / f"{name}.xcframework"

    patch_framework(local_framework, output, build_dir / "framework-patch", min_ios)
    remove_official_framework(output_root, pod_name, name)

    spec = load_json(spec_path)
    set_vendored_xcframework(spec, pod_name, name)
    write_json(spec_path, spec)


def all_patchable_pods(output_root: Path) -> list[str]:
    pods: list[str] = []
    for path in sorted(output_root.glob("*.podspec.json")):
        spec = load_json(path)
        name = spec.get("name")
        if name and name != "GoogleMLKit" and spec.get("source", {}).get("http"):
            pods.append(str(name))
    return pods


def main() -> int:
    parser = argparse.ArgumentParser(description="Download official pods and generate patched arm64-simulator xcframework pods.")
    parser.add_argument("config", type=Path, help="Config JSON, for example mlkit_v8_config.json or mlkit_v9_config.json")
    parser.add_argument(
        "--release-guard",
        choices=("ask", "yes", "no"),
        default="ask",
        help="Whether podspecs should inject a non-DEBUG #error guard header",
    )
    args = parser.parse_args()

    template_root = ROOT
    config_path = args.config.resolve()
    config = load_config(config_path)
    config_name = str(config.get("name") or config_path.stem)
    pods_config = config["pods"]
    output_root = configured_output_root(template_root, config)
    build_dir = output_root / ".build" / "official-patch"
    patch_pods = list(configured_default_pods(config))
    min_ios = str(config.get("min_ios", "")) or None
    release_guard = prompt_release_guard(args.release_guard)

    if not PATCH_SCRIPT.exists():
        raise SystemExit(f"Missing patch script: {PATCH_SCRIPT}")

    print(f"Output root: {output_root}")
    print(f"Config: {config_path}")
    print(f"MLKit config: {config_name}")
    print(f"Patch pods: {', '.join(patch_pods)}")
    print(f"Release guard: {'enabled' if release_guard else 'disabled'}")

    build_dir.mkdir(parents=True, exist_ok=True)
    write_official_podspecs(output_root, pods_config)

    for pod_name in pods_config:
        materialize_official_pod(output_root, pod_name, build_dir)

    for pod_name in patch_pods:
        patch_pod(output_root, pod_name, build_dir, min_ios)

    sync_configured_podspecs(output_root, pods_config)
    apply_release_guard(output_root, release_guard)

    build_root = output_root / ".build"
    if build_root.exists():
        shutil.rmtree(build_root)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
