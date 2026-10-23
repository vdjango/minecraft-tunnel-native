#!/usr/bin/env python3
"""Build CMake and keep the first useful compiler errors in CI summaries."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path
import re
import subprocess


ERROR_PATTERNS = (
    re.compile(r"\bfatal error\b", re.IGNORECASE),
    re.compile(r"\berror\s+C\d{3,5}\b", re.IGNORECASE),
    re.compile(r"\b(?:error|undefined reference|ld returned)\s*[: ]", re.IGNORECASE),
    re.compile(r"\b(?:LNK\d+|CMake Error)\b", re.IGNORECASE),
)


def is_error(line: str) -> bool:
    return any(pattern.search(line) for pattern in ERROR_PATTERNS)


def github_escape(message: str) -> str:
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def build(build_dir: str, config: str, parallel: str, log_path: Path) -> int:
    command = ["cmake", "--build", build_dir, "--config", config, "--parallel", parallel]
    tail: deque[str] = deque(maxlen=80)
    errors: list[str] = []
    with log_path.open("w", encoding="utf-8", newline="") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            tail.append(line.rstrip())
            if len(errors) < 40 and is_error(line) and line.rstrip() not in errors:
                errors.append(line.rstrip())
        status = process.wait()
    if status:
        # Never truncate the front: the root error usually precedes thousands
        # of cascading errors or unrelated deprecation warnings.
        diagnostics = "\n".join(errors)[:8000]
        context = "\n".join(tail)[-4000:]
        message = diagnostics + "\n--- build log tail ---\n" + context
        print("::error title=Native compilation failed::" + github_escape(message))
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", default="build")
    parser.add_argument("--config", default="Release")
    parser.add_argument("--parallel", default="2")
    parser.add_argument("--log", type=Path, default=Path("native-build.log"))
    args = parser.parse_args()
    return build(args.build_dir, args.config, args.parallel, args.log)


if __name__ == "__main__":
    raise SystemExit(main())
