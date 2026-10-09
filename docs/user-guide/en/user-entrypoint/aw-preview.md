# Install and demonstrate the AW Preview

[中文版](../../zh/user-entrypoint/aw-preview.md)

This Preview lets you try Qoder's before-tool security checks and persistent AW
execution records without compiling or assembling components yourself. Download
the artifacts from the **CI / AW Packages** run for the reviewed commit.
This integration Preview is not yet published through `anolisa install` or RPM.

## Install

The first target is Linux with glibc 2.39 or newer (Ubuntu 24.04), Python 3.11+
and a matching CPU architecture. CI builds x86_64; the same native build command
supports aarch64. Qoder CLI **1.1.64** and its model account are separate prerequisites
for the native demo. The offline demo does not need Qoder or an account.

One build produces three archives and `SHA256SUMS`:

| Archive | Contents |
| --- | --- |
| `aw-core-VERSION-linux-ARCH.tar.gz` | `bin/aw`: service, launcher and Qoder Hook adapter |
| `aw-provider-sec-core-VERSION-linux-ARCH.tar.gz` | AW Provider plus the Rust V2 sec-core CLI and daemon |
| `aw-all-in-one-VERSION-linux-ARCH.tar.gz` | Exactly the two component payloads above |

The independent Provider requires the core from the same version, source commit
and architecture. It includes embedded regex rules and needs no separately
installed sec-core. It does not include sec-core's Python CLI or other capability
runtimes. Manifests record the source commit, protocol, dependency, file hashes
and permissions. Checksums detect corruption; they are not publisher signatures.

Extract the downloaded Actions artifact ZIP first, then run in its directory:

```bash
sha256sum -c SHA256SUMS
AW_PREVIEW_VERSION=0.1.0-preview.1
AW_PREVIEW_ARCH="$(uname -m)"
AW_PREVIEW_BUNDLE="aw-all-in-one-$AW_PREVIEW_VERSION-linux-$AW_PREVIEW_ARCH"
tar -xzf "$AW_PREVIEW_BUNDLE.tar.gz"
sudo install -d -m 755 /opt/aw-preview
AW_PREVIEW_PREFIX="/opt/aw-preview/$AW_PREVIEW_VERSION"
sudo python3 "$AW_PREVIEW_BUNDLE/install.py" install --prefix "$AW_PREVIEW_PREFIX"
```

Alternatively, extract the core and Provider archives and run each `install.py
install` against the same fresh prefix, **core first**. Do not also install the
all-in-one into that prefix. The installer checks hashes and compatibility,
refuses symlink paths, pre-existing files and component overwrites, and records
ownership in `.aw-packages`. The bundled sec-core system daemon requires root, including root-owned config
and state paths. Install this self-contained distribution under a root-owned
prefix; run AW and Qoder as your normal user. A user-local installation is also
possible when connecting to an already deployed system backend. The installation
parent must belong to the installing user and not be writable by others.

## Offline demo

The included demo starts the installed AW and sec-core daemons, submits synthetic
before/after events, verifies pass/block candidates, restarts AW and reads its
previous audit records. The source checkout and Rust are not required. Run this self-contained offline
demo as root in a disposable container or on a test machine; its backend retains
the existing sec-core root requirement:

```bash
sudo install -d -m 700 /var/tmp/aw-preview-demo
sudo python3 "$AW_PREVIEW_BUNDLE/demo.py" --prefix "$AW_PREVIEW_PREFIX" \
  --work-dir /var/tmp/aw-preview-demo/offline
```

The work directory must be new, with root-owned ancestors for a bundled backend. Keep its absolute path short enough for Unix
sockets (under 90 bytes is a useful rule). `result.json`, process commands, logs
and audit files remain there. The script stops and waits for its own processes
on completion or failure. Synthetic events never execute scanned commands and
do not prove that a native Agent adopted a block.

## Native Qoder demo

First start the system backend using the foreground commands below. Then use a
fresh work directory and the absolute path of Qoder CLI 1.1.64 as your normal user.
With `--sec-socket`, the demo neither starts nor stops that external backend:

```bash
install -d -m 700 "$HOME/aw-preview"
python3 "$AW_PREVIEW_BUNDLE/demo.py" --prefix "$AW_PREVIEW_PREFIX" \
  --work-dir "$HOME/aw-preview/qoder-demo" --qoder /absolute/path/to/qodercli \
  --sec-socket /run/aw-preview-sec/daemon.sock
```

This also runs two model-backed Qoder sessions. The allowed `printf` must return `AW_PREVIEW_NATIVE` in a successful tool result. `git -c http.sslVerify=false --version > blocked-marker` matches
a built-in sec-core rule: Qoder must report the AW block and leave the marker
absent. That command neither accesses the network nor changes Git configuration,
even if the block fails. Model refusal, account failure or a missing tool call
fails the demo; it is not counted as successful security enforcement. Existing
Qoder Hooks still apply. The demo uses `--no-session-persistence`.

For ongoing use, generate a configuration outside the installation prefix:

```bash
install -d -m 700 "$HOME/aw-preview"
python3 "$AW_PREVIEW_BUNDLE/install.py" configure --prefix "$AW_PREVIEW_PREFIX" \
  --config "$HOME/aw-preview/aw.json" --state-dir "$HOME/aw-preview/state" \
  --socket /run/aw-preview-sec/daemon.sock --qoder /absolute/path/to/qodercli
```

Start the packaged backend in a foreground terminal. Keep this terminal open;
Ctrl-C stops the backend. Its data directory also stays outside the prefix:

```bash
sudo install -d -m 755 /run/aw-preview-sec /etc/aw-preview
sudo install -d -m 700 /var/lib/aw-preview
printf '{"stateDir":"/var/lib/aw-preview/skillsec"}\n' | sudo tee /etc/aw-preview/skillsec.json
sudo chmod 600 /etc/aw-preview/skillsec.json
sudo env AGENT_SEC_DATA_DIR=/var/lib/aw-preview/sec-data OTEL_SDK_DISABLED=true \
  "$AW_PREVIEW_PREFIX/libexec/aw/providers/sec-core/agent-sec-daemon" serve \
  --socket /run/aw-preview-sec/daemon.sock --skillsec-config /etc/aw-preview/skillsec.json
```

In another terminal, set `AW_PREVIEW_PREFIX` to the same installed path and run:

```bash
"$AW_PREVIEW_PREFIX/bin/aw" run --config "$HOME/aw-preview/aw.json" --agent qoder
"$AW_PREVIEW_PREFIX/bin/aw" status --config "$HOME/aw-preview/aw.json"
"$AW_PREVIEW_PREFIX/bin/aw" stop --config "$HOME/aw-preview/aw.json"
```

The configuration maps Qoder's case-sensitive `Bash` tool and `/command` input.
Before checks block on a risk verdict or execution failure; after checks only
observe successful tools. Other tools are unscanned. No result replacement,
portable approval, OS enforcement or other Agent adapter is delivered here.
AW starts on demand and remains available after Qoder exits. The explicit state
directory retains journals across reboots; socket and lock also live there.

## Upgrade, rollback and removal

Stop AW with its original configuration and stop the backend before switching
versions. Install the new build in a **new prefix** and generate a **new config**
pointing to it. Keep the previous prefix/config for rollback; restart those to
return to the previous build. This Preview does not migrate the journal format
or hot-reload config. Back up the external state before upgrading; use a fresh
state directory if the next Preview changes that format.

With both services stopped, remove only package-owned, unchanged files:

```bash
sudo python3 "$AW_PREVIEW_BUNDLE/install.py" uninstall --prefix "$AW_PREVIEW_PREFIX"
```

Modified package files cause removal to fail before deleting anything. Unknown
files, the prefix, its lock, external config and data are retained. Remove a demo
work directory yourself only after you no longer need its logs and audit records.

## Build artifacts (developers)

From a clean committed checkout, with Rust 1.97.1, a C compiler, pkg-config and
OpenSSL development headers installed:

```bash
python3 -B src/aw/packaging/preview/test_install.py
python3 -B src/aw/packaging/preview/build.py --version 0.1.0-preview.1 \
  --output src/aw/target/preview/packages
```

The output directory must be empty. The builder uses both workspaces' locked
dependencies and packages their release binaries. The workflow publishes Actions
artifacts only; existing component release tags, indices and workflows remain in
place. A unified release entrypoint is a subsequent delivery.

Package CI runs for changes to `src/aw/packaging/` or its workflow, and can also
be dispatched manually. Other AW/sec-core changes keep their existing component
gates; native Qoder sessions are an explicit local acceptance check.
