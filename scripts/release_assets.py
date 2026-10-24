#!/usr/bin/env python3
"""Prepare versioned release archives from tested CMake install directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    "ubuntu-24.04": "linux-x64",
    "windows-2022": "windows-x64",
    "macos-14": "macos-arm64",
    "macos-15-intel": "macos-x64",
}
TAG_PATTERN = re.compile(r"v(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)\.(\d+))?")


def versions(project: Path, tag: str = "", run_number: str = "0") -> tuple[str, str]:
    match = re.search(r"project\(buggcraft_native VERSION (\d+\.\d+\.\d+)\b", project.read_text(encoding="utf-8"))
    if not match:
        raise ValueError("Cannot read project version from CMakeLists.txt")
    base = match.group(1)
    if tag:
        release = TAG_PATTERN.fullmatch(tag)
        if not release or release.group(1) != base:
            raise ValueError(f"Release tag must match CMake version {base}: v{base} or v{base}-rc.1")
        if release.group(2):
            return tag[1:], f"{base}~{release.group(2)}.{release.group(3)}"
        return base, base
    if not run_number.isdecimal():
        raise ValueError("CI run number must be numeric")
    development = f"{base}~dev.{run_number}"
    return f"{base}-dev.{run_number}", development


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def package(runtime: Path, output: Path, runner: str, version: str) -> Path:
    windows = runner == "windows-2022"
    suffix = ".exe" if windows else ""
    library = ("bin/buggcraft_bridge.dll" if windows else
               "lib/libbuggcraft_bridge.dylib" if runner.startswith("macos-") else
               "lib/libbuggcraft_bridge.so")
    required = [f"bin/buggcraft-core{suffix}", f"bin/buggcraft-bridge{suffix}", library,
                "include/buggcraft/bridge.h", "share/buggcraft-native/LICENSE"]
    missing = [name for name in required if not (runtime / name).is_file()]
    if missing:
        raise ValueError("Incomplete runtime package: " + ", ".join(missing))
    if not re.fullmatch(r"[0-9A-Za-z.-]+", version):
        raise ValueError("Invalid archive version")
    metadata = runtime / "share/buggcraft-native/build-info.json"
    metadata.write_text(json.dumps({
        "version": version,
        "platform": PLATFORMS[runner],
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "build_url": (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                      f"{os.environ.get('GITHUB_REPOSITORY', 'vdjango/minecraft-tunnel-native')}/"
                      f"actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"),
    }, indent=2) + "\n", encoding="utf-8")
    output.mkdir(parents=True, exist_ok=True)
    stem = f"buggcraft-native-{version}-{PLATFORMS[runner]}"
    target = output / (stem + (".zip" if windows else ".tar.gz"))
    # Archive on the build runner, before artifact upload can lose Unix modes
    # or symlink identity. Keep one runtime/ root on every platform.
    if windows:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(runtime.rglob("*")):
                if path.is_file():
                    archive.write(path, Path("runtime") / path.relative_to(runtime))
    else:
        with tarfile.open(target, "w:gz") as archive:
            archive.add(runtime, arcname="runtime")
    target.with_name(target.name + ".sha256").write_text(f"{digest(target)}  {target.name}\n", encoding="ascii")
    return target


def verify_assets(directory: Path, version: str) -> None:
    required = [f"buggcraft-native-{version}-{platform}" + (".zip" if platform == "windows-x64" else ".tar.gz")
                for platform in PLATFORMS.values()]
    deb_version = version.replace("-rc.", "~rc.").replace("-beta.", "~beta.").replace("-alpha.", "~alpha.")
    required.append(f"buggcraft-node_{deb_version}_amd64.deb")
    for name in required:
        asset = directory / name
        checksum = directory / (name + ".sha256")
        if not asset.is_file() or not checksum.is_file():
            raise ValueError(f"Missing release asset or checksum: {name}")
        if checksum.read_text(encoding="ascii").strip() != f"{digest(asset)}  {name}":
            raise ValueError(f"Release checksum mismatch: {name}")
    sums = "".join(f"{digest(directory / name)}  {name}\n" for name in sorted(required))
    (directory / "SHA256SUMS").write_text(sums, encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    metadata = commands.add_parser("version")
    metadata.add_argument("--github-output", type=Path)
    archive = commands.add_parser("package")
    archive.add_argument("--runtime", type=Path, default=Path("package"))
    archive.add_argument("--output", type=Path, default=Path("build/release-assets"))
    archive.add_argument("--runner", choices=PLATFORMS, required=True)
    archive.add_argument("--version", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--directory", type=Path, default=Path("release"))
    verify.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.command == "version":
        tag = os.environ.get("GITHUB_REF_NAME", "") if os.environ.get("GITHUB_REF_TYPE") == "tag" else ""
        version, deb_version = versions(ROOT / "CMakeLists.txt", tag, os.environ.get("GITHUB_RUN_NUMBER", "0"))
        text = f"version={version}\ndeb_version={deb_version}\n"
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write(text)
        print(text, end="")
    elif args.command == "package":
        print(package(args.runtime, args.output, args.runner, args.version))
    else:
        verify_assets(args.directory, args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
