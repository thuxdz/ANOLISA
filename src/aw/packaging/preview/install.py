#!/usr/bin/env python3
"""Install immutable Preview components; keep configuration and data external."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"expected an absolute path without '..': {value}")
    for parent in [path, *path.parents]:
        if parent.is_symlink():
            raise ValueError(f"symlink in installation path: {parent}")
    return path


def relative(value):
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or str(path) != value:
        raise ValueError(f"invalid payload path: {value}")
    if path.parts[0] not in {"bin", "libexec", "share"}:
        raise ValueError(f"invalid payload directory: {value}")
    return path


def private_parent(path):
    absolute(path)
    info = path.parent.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError(f"parent must be owned by this user and not group/world writable: {path.parent}")


@contextmanager
def locked(prefix):
    fd = os.open(prefix / ".aw-install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def read_installed(prefix):
    directory = prefix / ".aw-packages"
    if directory.is_symlink():
        raise ValueError("package receipt directory is a symlink")
    return {p.stem: json.loads(p.read_text()) for p in directory.glob("*.json")}


def inspect_package(bundle):
    value = json.loads((bundle / "manifest.json").read_text())
    if value["format"] != 1 or not value["components"]:
        raise ValueError("unsupported package manifest")
    components = {}
    paths = set()
    for component in value["components"]:
        name = component["component"]
        if name not in {"aw-core", "aw-provider-sec-core"} or name in components:
            raise ValueError("unknown or duplicate component")
        if (component["format"] != 1 or component["os"] != "linux"
                or platform.system() != "Linux" or component["arch"] != platform.machine()
                or component["provider_protocol"] != "aw-provider/v1alpha1"):
            raise ValueError("incompatible platform or Provider protocol")
        required_files = ({"bin/aw"} if name == "aw-core" else {
            f"libexec/aw/providers/sec-core/{binary}"
            for binary in ("aw-provider-sec-core", "agent-sec-cli", "agent-sec-daemon")
        })
        if not required_files <= component["files"].keys():
            raise ValueError("incomplete component payload")
        for name_in_package, metadata in component["files"].items():
            path = relative(name_in_package)
            source = absolute(bundle / "payload" / path)
            if name_in_package in paths or not stat.S_ISREG(source.stat().st_mode):
                raise ValueError("duplicate or nonregular payload")
            if metadata["mode"] not in (0o644, 0o755) or digest(source) != metadata["sha256"]:
                raise ValueError(f"payload checksum or mode mismatch: {path}")
            paths.add(name_in_package)
        components[name] = component
    return components


def install(bundle, prefix):
    components = inspect_package(bundle)
    private_parent(prefix)
    created_prefix = not prefix.exists()
    if created_prefix:
        prefix.mkdir(mode=0o755)
        prefix.chmod(0o755)
    info = prefix.stat()
    if not prefix.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("unsafe installation prefix")
    created = []
    created_dirs = []
    try:
        with locked(prefix):
            try:
                installed = read_installed(prefix)
                if not created_prefix and not installed:
                    raise ValueError("refusing a prefix without AW package receipts")
                if set(installed) & set(components):
                    raise ValueError("component already installed; use a new prefix for upgrade or rollback")
                available = installed | components
                for component in components.values():
                    for name, version in component["requires"].items():
                        required = available.get(name, {})
                        if (required.get("version") != version
                                or required.get("source_commit") != component["source_commit"]
                                or required.get("arch") != component["arch"]):
                            raise ValueError(f"requires matching {name} from the same Preview build")
                    for name in component["files"]:
                        target = absolute(prefix / relative(name))
                        if target.exists():
                            raise ValueError(f"refusing to overwrite: {target}")
                for component in components.values():
                    for name, metadata in component["files"].items():
                        target = prefix / name
                        missing = []
                        parent = target.parent
                        while not parent.exists():
                            missing.append(parent)
                            parent = parent.parent
                        for directory in reversed(missing):
                            directory.mkdir()
                            directory.chmod(0o755)
                            created_dirs.append(directory)
                        with target.open("xb") as output, (bundle / "payload" / name).open("rb") as source:
                            created.append(target)
                            shutil.copyfileobj(source, output)
                        target.chmod(metadata["mode"])
                    receipt = prefix / ".aw-packages" / f"{component['component']}.json"
                    if not receipt.parent.exists():
                        receipt.parent.mkdir(mode=0o755)
                        receipt.parent.chmod(0o755)
                        created_dirs.append(receipt.parent)
                    with receipt.open("x") as output:
                        created.append(receipt)
                        output.write(json.dumps(component, indent=2) + "\n")
                    receipt.chmod(0o644)
                print(f"Installed {', '.join(components)} in {prefix}")
            except BaseException:
                for path in reversed(created):
                    path.unlink()
                for directory in reversed(created_dirs):
                    directory.rmdir()
                raise
    except BaseException:
        if created_prefix:
            (prefix / ".aw-install.lock").unlink(missing_ok=True)
            prefix.rmdir()
        raise


def configure(prefix, config, state_dir, socket, qoder):
    installed = read_installed(prefix)
    if set(installed) != {"aw-core", "aw-provider-sec-core"}:
        raise ValueError("install both AW core and sec-core Provider before configuring")
    for path in [config, state_dir, socket, qoder]:
        absolute(path)
    private_parent(config)
    private_parent(state_dir)
    if config.is_relative_to(prefix) or state_dir.is_relative_to(prefix):
        raise ValueError("configuration and state must be outside the immutable prefix")
    if len(os.fsencode(state_dir / "aw.sock")) >= 108:
        raise ValueError("AW state path is too long for a Unix socket")
    if subprocess.check_output([qoder, "--version"], timeout=10, text=True).strip() != "1.1.64":
        raise ValueError("this Preview requires Qoder CLI 1.1.64")
    data = configuration(prefix, state_dir, socket, qoder)
    fd = os.open(config, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(data + "\n")
    print(f"Created {config}; start the sec-core daemon before running AW")


def configuration(prefix, state_dir, socket, qoder):
    provider = prefix / "libexec/aw/providers/sec-core"
    spec = {
        "daemon": {"startup": "on_demand", "state_dir": str(state_dir), "endpoint": "auto"},
        "execution": {"guarantee": "native_hook", "default_event_budget_ms": 5000},
        "audit": {"enabled": True, "payload": "metadata_only"},
        "agents": {"qoder": {"adapter": "qoder", "argv": [str(qoder)]}},
        "providers": {"security": {
            "protocol": "aw-provider/v1alpha1",
            "transport": {"type": "stdio", "location": "agent", "argv": [
                str(provider / "aw-provider-sec-core"), "--cli", str(provider / "agent-sec-cli"),
                "--socket", str(socket)]},
            "timeout_ms": 2000, "max_output_bytes": 4096,
            "config": {"version": 1, "mode": "block", "tools": {
                "Bash": {"language": "bash", "input_pointer": "/command"}}},
        }},
        "events": {
            "tool.before": {"enabled": True, "required": True, "steps": [{
                "id": "scan-code", "provider": "security", "operation": "scan_code",
                "effects": ["observe", "block"], "on_error": "block"}]},
            "tool.after": {"enabled": True, "required": True, "steps": [{
                "id": "observe-completion", "provider": "security", "operation": "observe_tool",
                "effects": ["observe"], "on_error": "report"}]},
        },
    }
    return json.dumps({"apiVersion": "aw/v1alpha1", "kind": "AWConfiguration",
                       "metadata": {"name": "qoder-sec-core-preview"}, "spec": spec}, indent=2)


def uninstall(prefix):
    absolute(prefix)
    with locked(prefix):
        installed = read_installed(prefix)
        if not installed:
            raise ValueError("no AW package receipts")
        # Preflight every owned file before removing anything; user changes are retained.
        owned = []
        for component in installed.values():
            for name, metadata in component["files"].items():
                path = absolute(prefix / relative(name))
                if not path.is_file() or digest(path) != metadata["sha256"]:
                    raise ValueError(f"installed file modified or missing: {path}")
                owned.append(path)
        for path in owned:
            path.unlink()
        for name in installed:
            (prefix / ".aw-packages" / f"{name}.json").unlink()
        # Keep the prefix, lock and unknown files. Never traverse external config or data.
        print(f"Removed AW package files from {prefix}; external configuration and data retained")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["install", "configure", "uninstall"]:
        command = commands.add_parser(name)
        command.add_argument("--prefix", type=absolute, required=True)
        if name == "configure":
            for flag in ["config", "state-dir", "socket", "qoder"]:
                command.add_argument(f"--{flag}", type=absolute, required=True)
    args = parser.parse_args()
    if args.command == "install":
        install(Path(__file__).resolve().parent, args.prefix)
    elif args.command == "configure":
        configure(args.prefix, args.config, args.state_dir, args.socket, args.qoder)
    else:
        uninstall(args.prefix)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f"aw-preview: {error}")
