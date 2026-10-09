# 安装并演示 AW Preview

[English](../../en/user-entrypoint/aw-preview.md)

这个 Preview 可直接演示 Qoder 工具执行前的安全检查和 AW 持久审计，无需自行编译、
拼装组件。请下载对应提交的 **CI / AW Packages** 工作流产物。
这是集成预览版，尚未通过 `anolisa install` 或 RPM 发布。

## 安装

首个目标环境是 Linux、glibc 2.39 或更新版本（Ubuntu 24.04）、Python 3.11+，
并使用与 CPU 架构匹配的包。CI 构建 x86_64，同一原生构建命令也支持 aarch64。
原生演示还需单独安装 Qoder CLI **1.1.64** 并配置模型账号；离线演示不需要。

同一次构建生成三个压缩包和 `SHA256SUMS`：

| 压缩包 | 内容 |
| --- | --- |
| `aw-core-VERSION-linux-ARCH.tar.gz` | `bin/aw`：服务、启动器和 Qoder Hook 适配器 |
| `aw-provider-sec-core-VERSION-linux-ARCH.tar.gz` | AW Provider 及 Rust V2 sec-core CLI、daemon |
| `aw-all-in-one-VERSION-linux-ARCH.tar.gz` | 上述两份组件的相同产物 |

独立 Provider 要求 core 的版本、源码提交和架构一致。包内已包含 regex 规则，
无需另装 sec-core；不包含 sec-core Python CLI 或其他能力的运行环境。
清单记录源码提交、协议、依赖、文件哈希和权限。校验和用于检查文件损坏，不是发布者签名。

先解压下载的 Actions artifact ZIP，然后在解压目录执行：

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

也可分别解压 core 和 Provider，依次用各自的 `install.py install` 安装到同一个新前缀，
**先装 core**。不要再往该前缀安装 all-in-one。安装器校验哈希和兼容性，拒绝符号链接路径、
已有文件和组件覆盖，并在 `.aw-packages` 记录文件归属。包内 sec-core 系统 daemon 要求 root，配置和 state 路径也必须归 root 所有。
完整发行包安装到 root 拥有的前缀；AW 和 Qoder 仍使用普通用户运行。
若连接已部署的系统后端，也可在用户目录安装。安装父目录须归安装用户所有，且不可被其他用户写入。

## 离线演示

包内演示脚本启动已安装的 AW 和 sec-core daemon，提交模拟 before/after 事件，
校验放行/阻断候选结果，再重启 AW 查询此前审计。无需源码和 Rust。完整离线演示需在临时容器或测试机以 root 运行，保留 sec-core 原有权限要求：

```bash
sudo install -d -m 700 /var/tmp/aw-preview-demo
sudo python3 "$AW_PREVIEW_BUNDLE/demo.py" --prefix "$AW_PREVIEW_PREFIX" \
  --work-dir /var/tmp/aw-preview-demo/offline
```

工作目录必须尚不存在；启动包内后端时，其祖先目录须归 root 所有。绝对路径需足够短以容纳 Unix socket，建议少于 90 字节。
目录保留 `result.json`、进程命令、日志和审计文件；脚本结束或失败时停止并回收自身进程。
模拟事件不会执行待扫描的命令，也不能证明原生 Agent 已采用阻断结果。

## 原生 Qoder 演示

先按下方前台命令启动系统后端，再以普通用户使用新工作目录和 Qoder CLI 1.1.64 的绝对路径。
传入 `--sec-socket` 后，演示脚本不会启动或停止外部后端：

```bash
install -d -m 700 "$HOME/aw-preview"
python3 "$AW_PREVIEW_BUNDLE/demo.py" --prefix "$AW_PREVIEW_PREFIX" \
  --work-dir "$HOME/aw-preview/qoder-demo" --qoder /absolute/path/to/qodercli \
  --sec-socket /run/aw-preview-sec/daemon.sock
```

脚本额外运行两次真实模型驱动的 Qoder 会话。正常 `printf` 命令应在成功的工具结果中返回 `AW_PREVIEW_NATIVE`；
`git -c http.sslVerify=false --version > blocked-marker` 命中 sec-core 内置规则，
Qoder 必须报告 AW 阻断且不创建标记文件。即使阻断失效，这条命令也不访问网络、不修改
Git 配置。模型拒绝、账号失败或未调用工具均视为演示失败，不能算安全拦截成功。
已有 Qoder Hooks 继续生效；演示使用 `--no-session-persistence`。

日常使用时，在安装前缀之外生成配置：

```bash
install -d -m 700 "$HOME/aw-preview"
python3 "$AW_PREVIEW_BUNDLE/install.py" configure --prefix "$AW_PREVIEW_PREFIX" \
  --config "$HOME/aw-preview/aw.json" --state-dir "$HOME/aw-preview/state" \
  --socket /run/aw-preview-sec/daemon.sock --qoder /absolute/path/to/qodercli
```

在前台终端启动包内后端，保持该终端打开，Ctrl-C 停止后端。
其数据目录也位于安装前缀之外：

```bash
sudo install -d -m 755 /run/aw-preview-sec /etc/aw-preview
sudo install -d -m 700 /var/lib/aw-preview
printf '{"stateDir":"/var/lib/aw-preview/skillsec"}\n' | sudo tee /etc/aw-preview/skillsec.json
sudo chmod 600 /etc/aw-preview/skillsec.json
sudo env AGENT_SEC_DATA_DIR=/var/lib/aw-preview/sec-data OTEL_SDK_DISABLED=true \
  "$AW_PREVIEW_PREFIX/libexec/aw/providers/sec-core/agent-sec-daemon" serve \
  --socket /run/aw-preview-sec/daemon.sock --skillsec-config /etc/aw-preview/skillsec.json
```

另一终端将 `AW_PREVIEW_PREFIX` 设为同一安装路径后运行：

```bash
"$AW_PREVIEW_PREFIX/bin/aw" run --config "$HOME/aw-preview/aw.json" --agent qoder
"$AW_PREVIEW_PREFIX/bin/aw" status --config "$HOME/aw-preview/aw.json"
"$AW_PREVIEW_PREFIX/bin/aw" stop --config "$HOME/aw-preview/aw.json"
```

生成的配置映射区分大小写的 Qoder `Bash` 工具及 `/command` 输入。执行前遇到风险或
检查执行失败均阻断；执行后只观察成功工具。其他工具未扫描。本包不提供结果替换、
跨框架审批、OS 强制执行或其他 Agent 适配器。
AW 按需启动，Qoder 退出后仍可复用。显式 state 目录保留跨重启审计，socket 和锁也在该目录。

## 升级、回退和卸载

切换版本前，使用原配置停止 AW，并停止后端。将新版本安装到**新前缀**，
生成指向该前缀的**新配置**。保留旧前缀和配置，重新启动它们即可回退。
此 Preview 不迁移审计格式，也不热加载配置。升级前备份外部 state；
若下一个 Preview 改变格式，使用新的 state 目录。

两个服务均停止后，删除包拥有且未被修改的文件：

```bash
sudo python3 "$AW_PREVIEW_BUNDLE/install.py" uninstall --prefix "$AW_PREVIEW_PREFIX"
```

如包内文件被修改，卸载会在删除任何文件之前报错。未知文件、前缀及锁、外部配置和
数据均保留。确认不再需要日志和审计后，再自行删除演示工作目录。

## 构建产物（开发者）

在干净且已提交的工作树中，准备 Rust 1.97.1、C 编译器、pkg-config 和 OpenSSL 开发头文件：

```bash
python3 -B src/aw/packaging/preview/test_install.py
python3 -B src/aw/packaging/preview/build.py --version 0.1.0-preview.1 \
  --output src/aw/target/preview/packages
```

输出目录必须为空。构建使用两个 workspace 的锁定依赖，打包 release 二进制。
工作流只发布 Actions artifacts；现有组件的 release tag、索引和工作流保留。
统一 release 入口属于后续交付。

包级 CI 仅在 `src/aw/packaging/` 或其工作流变更时运行，也可手动触发。
其他 AW/sec-core 变更继续使用原有组件检查；真实 Qoder 会话作为显式的本地验收。
