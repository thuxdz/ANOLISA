#!/usr/bin/env python3
"""Verify installed binaries, real scans and durable audit; optionally run Qoder."""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import install


def command(args, **kwargs):
    return subprocess.check_output([str(arg) for arg in args], text=True, timeout=10, **kwargs)


def demonstrate(prefix, work, qoder, sec_socket=None):
    if sec_socket is None and os.geteuid() != 0:
        raise ValueError("bundled sec-core daemon requires root; run the offline demo in a root container "
                         "or supply --sec-socket for an already running system daemon")
    external_sec = sec_socket is not None
    install.private_parent(work)
    work.mkdir(mode=0o700)
    if len(os.fsencode(work / "state/aw.sock")) >= 108:
        raise ValueError("demo path too long for a Unix socket")
    provider = prefix / "libexec/aw/providers/sec-core"
    aw = prefix / "bin/aw"
    config = work / "aw.json"
    sec_socket = sec_socket or work / "sec.sock"
    state = work / "state"
    config.write_text(install.configuration(prefix, state, sec_socket, qoder or Path("/bin/false")))
    config.chmod(0o600)
    skillsec = work / "skillsec.json"
    skillsec.write_text(json.dumps({"stateDir": str(work / "skillsec")}))
    skillsec.chmod(0o600)
    env = dict(os.environ, AGENT_SEC_DATA_DIR=str(work / "sec-data"), OTEL_SDK_DISABLED="true")
    processes = []
    logs = []

    def start(args, name):
        log = (work / f"{name}.log").open("w")
        logs.append(log)
        process = subprocess.Popen([str(arg) for arg in args], cwd=work, env=env,
                                   stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        with (work / "processes.jsonl").open("a") as ledger:
            ledger.write(json.dumps({"pid": process.pid, "pgid": process.pid,
                                    "command": [str(arg) for arg in args], "cwd": str(work),
                                    "ports": [], "log": str(work / f"{name}.log"),
                                    "lifetime_seconds": 600, "stop": f"kill -TERM -- -{process.pid}"}) + "\n")
        return process

    def wait_socket(path, process):
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"daemon exited: see {work}")
            if path.exists():
                return
            time.sleep(0.05)
        raise TimeoutError(f"socket did not appear: {path}")

    def rpc(method, **kwargs):
        reply = command([aw, "request", "--socket", state / "aw.sock"],
                        input=json.dumps({"method": method, **kwargs}))
        return json.loads(reply)["result"]

    def stop(process):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        # Reap descendants in the owned group even if the leader already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    result = {"passed": False, "synthetic_events": [], "native_qoder": "not_tested"}
    try:
        command([aw, "validate", "--config", config])
        if not external_sec:
            sec = start([provider / "agent-sec-daemon", "serve", "--socket", sec_socket,
                         "--skillsec-config", skillsec], "sec-core")
            wait_socket(sec_socket, sec)
        service = start([aw, "serve", "--config", config, "--state-dir", state], "aw")
        wait_socket(state / "aw.sock", service)
        binding = rpc("bind", target="qoder", capabilities={
            "adapter": "qoder", "version": "synthetic-preview-demo", "entrypoint": "local-demo",
            "events": {"tool.before": ["observe", "block"], "tool.after": ["observe"]}},
            cwd=str(work), environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        instance = binding["instance_id"]
        audits = []
        for event, code, expected in [("tool.before", "printf AW_PREVIEW_SAFE", "observe"),
                                      ("tool.before", "git -c http.sslVerify=false --version", "block"),
                                      ("tool.after", "printf AW_PREVIEW_SAFE", "observe")]:
            handle = rpc("open_event", instance_id=instance, event={
                "name": event, "agent": {"adapter": "qoder", "binding_id": "qoder", "instance_id": instance},
                "session_id": "preview-demo", "tool": {"name": "Bash", "native_name": "Bash",
                "call_id": None, "input": {"command": code},
                "result": {"text": "AW_PREVIEW_SAFE"} if event == "tool.after" else None}, "native": {}})
            outputs = [rpc("invoke_step", event_id=handle["event_id"], instance_id=instance, step_id=step)
                       for step in handle["steps"]]
            assert outputs and all(output["status"] == "ok" for output in outputs), outputs
            assert any(effect["type"] == expected for output in outputs for effect in output["effects"]), outputs
            rpc("close_event", event_id=handle["event_id"], instance_id=instance)
            audit = rpc("audit", key=handle["event_id"])
            assert audit["terminal"] is True, audit
            audits.append(handle["event_id"])
            result["synthetic_events"].append({"event": event, "expected": expected, "results": outputs})
        rpc("unbind", instance_id=instance)
        generation = json.loads(command([aw, "status", "--socket", state / "aw.sock"]))["identity"]["generation"]
        rpc("stop")
        service.wait(timeout=5)
        service = start([aw, "serve", "--config", config, "--state-dir", state], "aw-restart")
        wait_socket(state / "aw.sock", service)
        status = json.loads(command([aw, "status", "--socket", state / "aw.sock"]))
        assert status["identity"]["generation"] != generation
        assert all(rpc("audit", key=key)["terminal"] for key in audits)
        result["audit_survives_restart"] = True
        if qoder:
            assert command([qoder, "--version"]).strip() == "1.1.64"
            def policy_blocks():
                def count(value):
                    if isinstance(value, dict):
                        return int(value.get("policy_block") is True) + sum(count(v) for v in value.values())
                    if isinstance(value, list):
                        return sum(count(v) for v in value)
                    return 0
                return sum(count(json.loads(line)) for path in state.rglob("*.jsonl")
                           for line in path.read_text().splitlines())

            previous_blocks = policy_blocks()
            for name, code in [("pass", "printf AW_PREVIEW_NATIVE"),
                               ("block", "git -c http.sslVerify=false --version > blocked-marker")]:
                prompt = (f"Use Bash exactly once with this command: {code}. "
                          "If denied, finish with DENIED. Never retry or use a different tool.")
                agent = start([aw, "run", "--config", config, "--agent", "qoder", "--",
                    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--max-model-request-retries", "0", "--max-output-tokens", "512",
                    "--output-format", "stream-json",
                    "--system-prompt", "Perform exactly one requested Bash call, then finish.",
                    "--allowed-tools", "Bash", "--tools", "Bash", "--no-session-persistence", "-p", prompt],
                    f"qoder-{name}")
                assert agent.wait(timeout=90) == 0, f"see qoder-{name}.log"
            def tool_results(name):
                rows = [json.loads(line) for line in (work / f"qoder-{name}.log").read_text().splitlines()
                        if line.startswith("{")]
                return [part for row in rows for part in row.get("message", {}).get("content", [])
                        if isinstance(part, dict) and part.get("type") == "tool_result"]

            assert any(not item.get("is_error") and "AW_PREVIEW_NATIVE" in str(item.get("content"))
                       for item in tool_results("pass")), "native printf did not execute"
            assert not (work / "blocked-marker").exists()
            assert policy_blocks() > previous_blocks, "no native policy block in durable audit"
            assert any("Tool blocked by AW policy" in str(item.get("content"))
                       for item in tool_results("block")), "Qoder did not report the AW block"
            result["native_qoder"] = "pass_executed_block_not_executed"
        rpc("stop")
        service.wait(timeout=5)
        result["passed"] = True
    finally:
        for process in reversed(processes):
            stop(process)
        for log in logs:
            log.close()
        result["cleanup"] = {"owned_processes_exited": all(p.poll() is not None for p in processes),
                             "aw_socket_removed": not (state / "aw.sock").exists()}
        (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"interrupted by signal {signum}")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=install.absolute, required=True)
    parser.add_argument("--work-dir", type=install.absolute, required=True,
                        help="new private directory; retained with logs and audit evidence")
    parser.add_argument("--qoder", type=install.absolute,
                        help="optional Qoder CLI 1.1.64; uses your existing model account")
    parser.add_argument("--sec-socket", type=install.absolute,
                        help="use an existing system sec-core daemon; do not start or stop it")
    args = parser.parse_args()
    demonstrate(args.prefix, args.work_dir, args.qoder, args.sec_socket)
