#!/usr/bin/env python3
"""Build the current clean checkout and package its native Linux Preview."""

import argparse
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "src/aw/scripts"))
from check import run  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "aarch64"}:
        parser.error("requires native Linux x86_64 or aarch64")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO, timeout=10):
        parser.error("commit the checkout before building a Preview")
    aw = REPO / "src/aw"
    sec = REPO / "src/agent-sec-core/v2"
    # Explicit target directories keep both build and packaging on the same outputs.
    for root, packages in [(aw, ["aw-service", "aw-provider-sec-core"]),
                           (sec, ["asc-cli", "asc-daemon"])]:
        command = ["cargo", "+1.97.1", "build", "--release", "--locked",
                   "--target-dir", str(root / "target")]
        for package in packages:
            command.extend(["-p", package])
        run(command, root, timeout=1200)
    run([sys.executable, "-B", str(HERE / "package.py"), "--version", args.version,
         "--arch", platform.machine(), "--aw-bin-dir", str(aw / "target/release"),
         "--sec-core-bin-dir", str(sec / "target/release"),
         "--output", str(args.output.resolve())], REPO, timeout=180)


if __name__ == "__main__":
    main()
