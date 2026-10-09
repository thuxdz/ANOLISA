#!/usr/bin/env python3
"""Package one pinned build into an AW core, Provider and combined Preview."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def elf(path, machine):
    with path.open("rb") as stream:
        header = stream.read(20)
    expected = {"x86_64": 62, "aarch64": 183}[machine]
    if (len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(header[18:20], "little") != expected
            or not os.access(path, os.X_OK)):
        raise ValueError(f"expected executable Linux {machine} ELF: {path}")


def package(args):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-preview\.[0-9]+", args.version):
        raise ValueError("version must be X.Y.Z-preview.N")
    if platform.system() != "Linux":
        raise ValueError("Preview packaging requires Linux")
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, timeout=10
    ).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"],
                               cwd=REPO, timeout=10):
        raise ValueError("commit the source tree before packaging")
    binary_sources = {
        "aw-core": {"bin/aw": args.aw_bin_dir / "aw"},
        "aw-provider-sec-core": {
            f"libexec/aw/providers/sec-core/{name}": directory / name
            for name, directory in [
                ("aw-provider-sec-core", args.aw_bin_dir),
                ("agent-sec-cli", args.sec_core_bin_dir),
                ("agent-sec-daemon", args.sec_core_bin_dir),
            ]
        },
    }
    for sources in binary_sources.values():
        for path in sources.values():
            elf(path, args.arch)
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        raise ValueError("output directory must be empty")
    outputs = []
    with tempfile.TemporaryDirectory(prefix="stage-", dir=args.output) as temporary:
        stage = Path(temporary)
        manifests = {}
        for component, sources in binary_sources.items():
            root = stage / component
            for target, origin in sources.items():
                destination = root / "payload" / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, destination)
                destination.chmod(0o755)
            notices = root / "payload/share/doc" / component
            notices.mkdir(parents=True)
            shutil.copyfile(REPO / "LICENSE", notices / "LICENSE")
            shutil.copyfile(REPO / "NOTICE", notices / "NOTICE")
            for notice in notices.iterdir():
                notice.chmod(0o644)
            entries = {
                str(p.relative_to(root / "payload")): {
                    "sha256": digest(p), "mode": p.stat().st_mode & 0o777,
                }
                for p in sorted((root / "payload").rglob("*")) if p.is_file()
            }
            manifest = {
                "format": 1, "component": component, "version": args.version,
                "source_commit": source, "os": "linux", "arch": args.arch,
                "provider_protocol": "aw-provider/v1alpha1", "files": entries,
                "requires": {} if component == "aw-core" else {"aw-core": args.version},
            }
            manifests[component] = manifest
        for name, selected in [
            ("aw-core", ["aw-core"]),
            ("aw-provider-sec-core", ["aw-provider-sec-core"]),
            ("aw-all-in-one", ["aw-core", "aw-provider-sec-core"]),
        ]:
            root = stage / f"{name}-{args.version}-linux-{args.arch}"
            root.mkdir()
            for component in selected:
                shutil.copytree(stage / component / "payload", root / "payload", dirs_exist_ok=True)
            (root / "manifest.json").write_text(json.dumps(
                {"format": 1, "components": [manifests[c] for c in selected]}, indent=2) + "\n")
            for script in ("install.py", "demo.py"):
                shutil.copyfile(HERE / script, root / script)
            for language, name in (("en", "README.md"), ("zh", "README_zh.md")):
                guide = (REPO / f"docs/user-guide/{language}/user-entrypoint/aw-preview.md").read_text()
                guide = guide.replace("../../zh/user-entrypoint/aw-preview.md", "README_zh.md")
                guide = guide.replace("../../en/user-entrypoint/aw-preview.md", "README.md")
                (root / name).write_text(guide)
            target = args.output / f"{root.name}.tar.gz"
            if target.exists():
                raise ValueError(f"refusing to replace artifact: {target}")
            with tarfile.open(target, "w:gz") as archive:
                archive.add(root, arcname=root.name)
            outputs.append(target)
    checksums = args.output / "SHA256SUMS"
    with checksums.open("x") as output:
        for path in outputs:
            output.write(f"{digest(path)}  {path.name}\n")
    print(json.dumps({"source_commit": source, "artifacts": [str(p) for p in outputs]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", choices=["x86_64", "aarch64"], required=True)
    parser.add_argument("--aw-bin-dir", type=Path, required=True)
    parser.add_argument("--sec-core-bin-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    package(parser.parse_args())


if __name__ == "__main__":
    main()
