# 启动前网络检查与 Clash 检测

> 2026-11-05 落地注记：代码已落地，未提交。`python3 -m unittest discover -s tests` 84 项通过（Python 3.11、3.10），用例见 `tests/test_netcheck.py`。源码位置（落地后）：`src/multi_grok/netcheck.py:99` `run_checks`、`:129` `gate`；`src/multi_grok/clashfind.py:98` `find_controller`；`src/multi_grok/config.py:61` `NetCheck`、`:96` `ClashApi`；`src/multi_grok/launcher.py:106` 检查段；`src/multi_grok/cli.py:36` 退出码 5、`:1419` `_net_gate`、`:1439` `cmd_netgate`、`:1453` `cmd_netcheck`、`:1567` `cmd_clash_api`。`netcheck` 按改完后是否开着 Clash 检测决定是否自动查找控制接口；`apply -f` 在解析前去掉文件里的 `clash_api`，该值不合法也不会让 apply 失败。CI 的 Python 3.8 一项要推送后才有结果。
>
> 2026-11-05 方案注记：基线 `main` `d55bcea`（v0.3.0），下文 `file:line` 都指这个基线。参照 multi-codex 0.14.0 的同名功能（multi-codex 仓库 `docs/feature/feature-network-check.md`、`docs/feature/feature-clash-split.md`，代码 `src/multi_codex/netcheck.py`、`src/multi_codex/clashfind.py`）。本轮只做 macOS / Linux，Windows 部分随 0.5.0 的 Windows 支持一起做。

## 1. 背景

用户要求把 multi-codex 0.13.0～0.15.0 的功能搬到 multi-grok（2026-11-05）。用户已拍板：

1. 版本范围是 0.13～0.15，用户原话里的第二个 “0.14.0” 是 0.13.0；
2. 分两轮：本轮 0.4.0 做网络检查、Clash 检测、补全取值和会话别名，只覆盖 macOS / Linux；原生 Windows 放到 0.5.0。

本文覆盖 multi-codex 0.14.0 的网络检查部分：按账号设置检查档位，开启后启动 Grok 前检查一次，不通过就拒绝启动（退出码 5）。补全取值与会话别名见 `docs/feature/feature-completion-values.md`。

已核实的现状：

- 账号模型 `Account`（`src/multi_grok/config.py:51-84`）没有网络检查字段。`parse_config` 遇到未知顶层键直接报错（`config.py:192-194`），账号里的未知键同样报错（`config.py:245-248`）。
- 账号代理只接受 `inherit`、`off`、`http(s)://HOST:PORT`，不接受 SOCKS（`config.py:343-382`）。
- 启动命令 `launcher.render`（`src/multi_grok/launcher.py:61-94`）是 `/bin/sh` 脚本：查软链与目录（`:69-76`）→ 设 `GROK_HOME`、清身份变量、写 env 与代理（`:77-84`）→ 查 grok（`:87-90`）→ `exec grok`（`:91`）。整个过程不经 Python。
- `install.sh` 生成的 `multi-grok` 包装是 `exec '${PYTHON}' -m multi_grok`（`install.sh:119`），`-m` 会把当前目录放到模块搜索路径最前面。
- 会启动 Grok 的入口（`grep -n "subprocess.call\|subprocess.run\|exec grok\|exec \"\$target\"" src/multi_grok/*.py` 全集；`subprocess.run` 只命中 `usage --history` 的 `cli.py:1095`）：

| 入口 | 调用链（基线） |
| ---- | ---- |
| `grok-<名>` | sh 脚本 `exec grok`（`launcher.py:91`） |
| `grok-auto` | 路由脚本 `exec "$target" "$@"`，即账号启动命令（`routes.py:94`）；没有匹配也没有默认账号时 `exec grok`（`routes.py:100`），不属于任何账号 |
| `login` | `cmd_login`（`cli.py:734-749`），`subprocess.call` 在 `:749` |
| `run NAME --` | `cmd_run` → `_exec_grok`（`cli.py:965`、`:861-869`） |
| `run NAME -- 命令` | `cmd_run` → `_prepare` → `subprocess.call`（`cli.py:966-969`） |
| `code` | `cmd_code` → `_prepare` → `subprocess.call`（`cli.py:1258-1274`） |
| `mcp` | `cmd_mcp` → `_exec_grok`（`cli.py:986-987`） |
| `usage` | `cmd_usage` 调 `grok usage`（`cli.py:990` 起） |

- Grok 实际连接的会话服务地址：用户指南没有写默认值（`26-config-reference.md:195` 只说明 `endpoints.cli_chat_proxy_base_url` 可配置）。grok 1.0.70 程序里的字符串是 `https://cli-chat-proxy.grok.com/v1`（`strings ~/.grok/downloads/grok-1.0.70-macos-aarch64`）。2026-11-05 本机实测 `https://cli-chat-proxy.grok.com/cdn-cgi/trace` 返回 200，内容含 `h=cli-chat-proxy.grok.com` 与 `loc=US`。

## 2. 目标 / 非目标

**目标**

1. 账号可设 `off` / `basic` / `strict`（含国家）三档，Clash 的 TUN 与全局模式两项检测可以分别开关。开启后，`grok-<名>`、`grok-auto`、`run`、`login`、`code` 在启动 Grok 之前检查一次，不通过就拒绝启动，说明哪一项没过，给出本次跳过和关闭检查的写法，退出码 5。
2. `multi-grok clash-api auto` 从本机正在运行的 Clash / mihomo 找到控制接口并保存；开启 Clash 检测而接口未设置时自动找一次。
3. 未开启检查的账号，启动命令内容、`config.json` 内容都与 0.3.0 逐字节相同。

**非目标**

- 不保证 Grok 运行中不直连，也不保证 Grok 真的走账号代理。用户指南没有写 Grok 在代理不可用时会不会改为直连，这一点未验证。
- 不检查 `mcp`、`usage`、`sessions`、`handoff`、`export`、`import`：它们不开始对话。`run NAME -- 任意命令` 照样检查，因为命令可能就是 `grok`。
- 不做运行中的周期检查，不缓存检查结果，不修改 Clash 的任何设置。
- 不支持 Windows 命名管道形式的 Clash 接口（0.5.0 再做）。
- 不在 `list`、`doctor` 里显示网络检查设置（multi-codex 也没有）。

## 3. 假设与约束

- **查询地址**：`https://cli-chat-proxy.grok.com/cdn-cgi/trace`。返回逐行 `key=value`，取 `loc=`。这是 Cloudflare 的诊断地址，xAI 没有承诺它的格式，属于外部依赖。
- **国家代码**：两位字母，存大写，比较不分大小写，默认 `US`。
- **加强档只支持 `http://` 代理**：经代理访问时用 HTTP `CONNECT` 隧道。`https://` 代理（连代理本身也要 TLS）需要在 TLS 里再套一层 TLS，标准库做不到，所以设置时拒绝，启动时遇到也判失败。基础档只连端口，`https://` 代理也能用。
- **基础 / 加强档需要账号代理是 URL**。代理为 `inherit` / `off` 时设置命令拒绝；之后被 `proxy` / `set` / `apply -f` 改成 `inherit` / `off` 的，启动时判失败并说明。配置解析时不拒绝，免得一条 `proxy` 命令让整份配置读不出来。
- **mihomo 控制接口契约**沿用 multi-codex 已核过的结论（multi-codex `docs/feature/feature-network-check.md` §3 第 68-74 行，mihomo v1.19.32 `hub/route/configs.go:25-27`、`:133-136`，`hub/route/server.go:120-121`、`:336-363`）：`GET /configs` 返回 JSON，`mode` 是小写 `global` / `rule` / `direct`，`tun.enable` 反映 TUN 实际是否在运行；TCP 接口设了 secret 时要 `Authorization: Bearer <secret>`，否则 401；unix 套接字不鉴权。
- **检查版启动命令需要 `multi-grok` 在 PATH 上**：找不到时退出 127 并说明。未开启检查的账号不受影响。
- 只用标准库：`http.client`（它不读环境变量里的代理，直连请求因此真的直连）、`ssl`、`socket`。
- **新旧版本不能混用**：0.3.0 读到含 `net_check` / `clash_api` 的配置会报 “unknown keys” 并退出 1（`config.py:192-194`、`:245-248`），不会悄悄当作未开启。启动命令已带检查段、而 PATH 上的 `multi-grok` 是 0.3.0 时，`netgate` 被 argparse 当作无效子命令，退出 2，Grok 不启动。配置版本号保持 1。

## 4. 涉及模块

新增文件：

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_grok/netcheck.py` | 新文件 | 新增 | 端口、出口国家（直连 / 经 http 代理）、Clash 状态；`run_checks`、`gate`、`scope_text`、`failure_text`、`read_clash_state` |
| `src/multi_grok/clashfind.py` | 新文件 | 新增 | `find_controller`：从进程参数与配置文件收集候选，逐个 `GET /configs` 验证 |
| `tests/test_netcheck.py` | 新文件 | 新增 | §8 的用例：假 HTTP 代理、假 TLS trace 服务、假 mihomo（TCP 与 unix 套接字） |
| `tests/data/netcheck-cert.pem`、`netcheck-key.pem` | 新文件 | 新增 | 从 multi-codex `tests/data/` 复制的 `127.0.0.1` / `localhost` 自签证书，只用于 TLS 用例 |

修改既有文件：

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `config.py` 常量 | `:28` 之后 | 新增 | `NET_CHECK_LEVELS`、`NETCHECK_SKIP_ENV`、`DEFAULT_COUNTRY` |
| `config.py` `Account` | `:54-84` | 修改 | 新增 `net_check` 参数与属性；`copy` 带上；`to_dict` 只在开启时写 `net_check` |
| `config.py` `Config` | `:88-153` | 修改 | 新增 `clash_api` 参数与属性；`copy` 带上；`to_dict` 只在设置时写 `clash_api` |
| `config.py` 解析 | `:192`、`:237-238`、`:245-246`、`:271` | 修改 | 允许并解析 `clash_api`、账号的 `net_check` |
| `config.py` 新类与函数 | `:388` 之后（`_check_port` 后） | 新增 | `NetCheck`、`ClashApi`、`normalize_country`、`normalize_clash_address`、`_parse_net_check`、`_parse_clash_api` |
| `platform.py` | `:43` 之后 | 新增 | `list_process_argv`、`_proc_argv`、`_ps_argv`、`split_ps_command`（从 multi-codex `platform.py:544-623` 搬 POSIX 部分） |
| `launcher.py` `render` | `:61`、`:76` 之后 | 修改 | 新增仅限关键字参数 `net_check=False`；为真时在目录检查之后插入检查段；为假时输出不变 |
| `accounts.py` | `:183-184`、`:283-284` | 修改 | 两处 `render` 调用传 `net_check=account.net_check.active` |
| `cli.py` 退出码 | `:33` 之后 | 新增 | `EXIT_NETCHECK = 5` |
| `cli.py` `run` | `:45` 之后 | 新增 | 隐藏子命令 `netgate NAME`：在 `_split_forwarded` 与 argparse 之前拦截 |
| `cli.py` 帮助与示例 | `:95-100`、`:125-150`、`:153-242` | 修改 | “Account settings” 组加 `netcheck, clash-api`；补 `COMMAND_HELP`、`COMMAND_EXAMPLES` |
| `cli.py` 参数 | `:395` 之前 | 新增 | `netcheck`、`clash-api` 两个子解析器 |
| `cli.py` `_dispatch` | `:440-499` | 修改 | 分派 `netcheck`、`clash-api` |
| `cli.py` `cmd_apply` | `:696-705` | 修改 | `-f` 时 `clash_api` 沿用本机当前值，文件里有就提醒已忽略 |
| `cli.py` `cmd_login` | `:743`（目录检查 `return`）之后、`:744`（找 grok）之前 | 修改 | 调 `_net_gate` |
| `cli.py` `_prepare` / `_exec_grok` | `:843-869` | 修改 | 新增可选参数 `gate_hint`，有值时在目录检查（`:851-853`）之后、找 grok（`:854-856`）之前调 `_net_gate` |
| `cli.py` `cmd_run` | `:965`、`:966` | 修改 | 两条分支都传 `gate_hint` |
| `cli.py` `cmd_code` | `:1261` | 修改 | 传 `gate_hint` |
| `cli.py` 新函数 | `cmd_completion`（`:1319`）之前 | 新增 | `cmd_netgate`、`_net_gate`、`cmd_netcheck`、`cmd_clash_api`、`_apply_netcheck_changes`、`_find_clash` |
| `completion.py` | `POSITIONAL_KINDS` `:17-24` | 修改 | `netcheck` 第 1 个参数补账号名（其余取值见补全方案） |
| `tests/helpers.py` `isolated_home` | `:23` | 修改 | `keys` 加 `MULTI_GROK_SKIP_NETCHECK`、`MULTI_GROK_TEST_TRACE`、`MULTI_GROK_TEST_CA`、`MULTI_GROK_TEST_CLASH_PROCESSES`、`MULTI_GROK_TEST_CLASH_CONFIGS`，测完还原 |
| `README.md`、`README.zh-CN.md` | “Commands” 表（`README.md:123-152`）、退出码句（`:152`） | 修改 | 新命令、网络检查一节、局限、退出码 5 |
| `CHANGELOG.md` | 顶部 | 修改 | 0.4.0 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 配置

账号字段 `net_check`，只在开启时写出：

```json
"net_check": {"level": "strict", "country": "US", "clash_tun": true, "clash_global": false}
```

- `level` ∈ `off` / `basic` / `strict`；`clash_tun`、`clash_global` 是布尔值，与 `level` 互相独立。
- `active = level != "off" or clash_tun or clash_global`。不开启时 `to_dict` 不写这个键，未开启账号的 `config.json` 与 0.3.0 一致。
- multi-grok 没发过只有一个 `clash` 开关的版本，不需要兼容旧字段；`clash` 子键按未知键拒绝。

解析规则（与本仓库顶层、账号的严格风格一致，`config.py:192-194`、`:245-248`）：

| 键 | 必填 | 默认 | 类型与取值 | 错误 |
| ---- | ---- | ---- | ---- | ---- |
| `net_check` | 否 | 不开启 | 对象 | 不是对象报 ConfigError |
| `net_check.level` | 否 | `off` | `off` / `basic` / `strict` | 其它值报错 |
| `net_check.country` | 否 | `US` | 两位字母，存大写 | 其它报错 |
| `net_check.clash_tun`、`clash_global` | 否 | `false` | 布尔 | 非布尔报错 |
| `net_check` 里其它键 | 否 | 无 | 无 | 报 “unknown keys” |
| `clash_api` | 否 | 未设置 | 对象 | 不是对象报错 |
| `clash_api.address` | 是 | 无 | 按下文规范化 | 缺失或不合法报错 |
| `clash_api.secret` | 否 | 空 | 单行字符串 | 非字符串或含换行报错 |
| `clash_api` 里其它键 | 否 | 无 | 无 | 报 “unknown keys” |

开启时 `to_dict` 固定写出 `level`、`country`、`clash_tun`、`clash_global` 四个键；`clash_api` 写 `address`，secret 非空时再写 `secret`。

顶层字段 `clash_api`，只在设置时写出：

```json
"clash_api": {"address": "http://127.0.0.1:9097", "secret": "..."}
```

- `address` 规范成 `http://HOST:PORT` 或 `unix:/绝对路径`；写成 `HOST:PORT` 时补 `http://`；带路径、用户名或其它 scheme 的拒绝。命名管道地址报 “only supported on Windows”。
- `secret` 是单行字符串，空串表示没有、不写出。`config.json` 本来就是 0600（`config.py:182`）。
- `apply -f FILE` 不从文件里取 `clash_api`：它是这台机器的设置。文件里有这个键时提醒一句 “ignored”，然后沿用当前值。

#### 5.1.2 检查项（`netcheck.py`）

按 `clash` → `proxy-port` → `direct-country` → `proxy-country` 的顺序检查开启的项：

| 项 | 条件 | 超时 |
| ---- | ---- | ---- |
| `clash` | 开了 `clash_global` 时 `mode` 必须是 `global`；开了 `clash_tun` 时 `tun.enable` 必须为真；只开 `clash_tun` 时还拒绝 `direct` 模式 | 3 s |
| `proxy-port` | 账号代理的 TCP 端口能连上 | 3 s |
| `direct-country` | 不走代理访问 trace 地址，`loc` 等于国家 | 8 s |
| `proxy-country` | 经账号 http 代理的 `CONNECT` 隧道访问同一地址，`loc` 等于国家 | 8 s |

- 放行判断时第一项失败就停；`netcheck NAME` 诊断时逐项列出全部结果。不重试。
- 失败说明按异常分类：DNS 失败、证书校验失败、TLS 错误、超时、连接被拒、代理拒绝 CONNECT、HTTP 状态码、没有 `loc=`、`loc` 不符。说明里不出现 secret。
- 响应最多读 64 KB。
- 测试注入：`MULTI_GROK_TEST_TRACE=HOST:PORT` 把 trace 目标换成本地假服务；`MULTI_GROK_TEST_CA=文件` 指定 CA。两者只在测试里设置。

#### 5.1.3 检查版启动命令

`render(..., net_check=True)` 在 `launcher.py:76`（目录检查的 `fi`）之后插入：

```sh
if ! command -v multi-grok >/dev/null 2>&1; then
  echo 'multi-grok: multi-grok not found in PATH; it runs the network check of this account' >&2
  exit 127
fi
(cd / && exec multi-grok netgate home) || exit $?
unset MULTI_GROK_SKIP_NETCHECK
```

- 先 `cd /`：`multi-grok` 包装用 `${PYTHON} -m` 运行（`install.sh:119`），在用户项目目录里运行时，项目里的 `json.py`、`ssl.py` 会顶替标准库。切目录只发生在子 shell 里，grok 仍在用户当前目录启动。
- 按 PATH 找 `multi-grok`，不写死路径。
- 检查在子进程里跑，删不掉外层 shell 的跳过变量，所以通过后由 sh 删掉，grok 和它的子进程不会继承 “跳过”。
- 账号名已由 `validate_name` 限定字符，仍用 `shlex.quote`。
- `net_check=False` 时输出与基线逐字节相同。

#### 5.1.4 放行判断与入口

`cli.run` 在 `cli.py:45` 之后拦截 `argv[0] == "netgate"`，交给 `cmd_netgate(argv[1:])`：参数个数不是 1 时退出 2；账号未登记时退出 1；否则调 `_net_gate(config, account, "grok-" + 名)`，通过返回 0。

`_net_gate(config, account, hint)`：

1. 账号未开启检查，或账号目录不存在 → 放行（目录错误交给原有路径报告）。
2. `MULTI_GROK_SKIP_NETCHECK` 去掉首尾空白后等于 `1` → 放行，stderr 打 `multi-grok: network check skipped for this run (MULTI_GROK_SKIP_NETCHECK=1)`，并从 `os.environ` 删掉它。
3. 跑检查（在找 grok 之前；与启动命令里检查段排在 `command -v grok` 之前一致，`launcher.py:87-90`）。全部通过时 stderr 打 `multi-grok: network check passed for account NAME (scope; N ms)`，放行。
4. 不通过时 stderr 打多行说明，返回 `EXIT_NETCHECK`（5）：

```text
multi-grok: network check failed for account home (strict, US): proxy-country: loc is JP, expected US (via http://127.0.0.1:7890) (812 ms)
  passed: proxy-port, direct-country
  skip once: MULTI_GROK_SKIP_NETCHECK=1 grok-home
  turn off:  multi-grok netcheck home off
```

开了 Clash 检测时 “turn off” 一行末尾加 ` --clash off`。检查中按 Ctrl+C 返回 130。

接入点：

- `cmd_login`：在 `cli.py:743`（目录检查）之后、`:744`（找 grok）之前，与 `_prepare` 一样先检查网络再找 grok，提示 `multi-grok login NAME`。
- `_prepare(name, need_grok, gate_hint=None)`：目录检查（`:851-853`）之后、找 grok（`:854-856`）之前，`gate_hint` 有值就调 `_net_gate`；不通过返回 `(None, None, 5)`。`_exec_grok` 增加同名参数并往下传。
- `cmd_run` 的两个分支传 `"multi-grok run NAME"`，`cmd_code` 传 `"multi-grok code NAME"`，`cmd_mcp` 不传。
- `os.environ` 先删掉跳过变量，再由 `build_env(dict(os.environ), ...)` 构造环境，所以经 Python 启动的 grok 也不会继承 “跳过”。

`grok-auto` 不改：它 `exec` 账号启动命令，检查发生在启动命令里。

联网模块 `netcheck` 只在需要时导入（`_net_gate` 发现账号已开启、或执行 `netcheck` / `clash-api` 时），未开启检查的启动不多导入 `http.client`、`ssl`。

#### 5.1.5 命令

```text
multi-grok netcheck NAME [off|basic|strict] [--country CC] [--clash on|off] [--clash-tun on|off] [--clash-global on|off] [--dry-run] [--verbose]
multi-grok clash-api [ADDRESS|auto|off] [--secret-stdin] [--dry-run] [--verbose]
```

参数声明：档位位置参数 `nargs="?"`、`choices=("off", "basic", "strict")`；`--clash`、`--clash-tun`、`--clash-global` 用 `choices=("on", "off")`（补全方案从 `choices` 读取取值，见 `docs/feature/feature-completion-values.md` §3）。

`netcheck NAME` 不带任何设置参数时只读：显示 `network check of NAME: strict, US, clash`（或 `network check is off for NAME`），立即逐项检查并按 `ok|fail 项 说明 (N ms)` 列出；全过退出 0，否则退出 5。不加锁。

`netcheck` 写形态（`_apply_netcheck_changes`，按改完后的结果校验，不合法退出 2，什么都不写）：

- `--clash on|off` 同时设两项；与 `--clash-tun` / `--clash-global` 同时给出时退出 2。
- 档位 `basic` / `strict` 而账号代理不是 URL → 退出 2，并提示先 `multi-grok proxy NAME 7890`。
- `strict` 且代理是 `https://` → 退出 2。
- 改完后档位不是 `strict` 却给了 `--country` → 警告 “country is only used by strict”；账号仍处于开启状态（basic 或开着 Clash 检测）时 country 照常写出（保存但不使用），账号整体未开启时不写出。
- 只开 `clash_global` 不开 `clash_tun` → 警告：全局模式只管送到代理端口的流量，直连抓不到。
- 开启了 Clash 检测而 `clash_api` 未设置 → 先自动查找一次（§5.1.6），找到就随本次写入一起保存，stderr 打 `found Clash controller ADDRESS (mode=..., tun ...)`；找不到只警告 “launches will be refused until you run `multi-grok clash-api auto`”，照样写入。
- 走 `_commit` 写入；成功且不是 `--dry-run` 时 stdout 打 `network check of NAME: <scope>` 或 `off`。

`clash-api` 不带地址时只读：`Clash controller: ADDRESS (secret set|not set)` 或 `Clash controller is not set`，不显示 secret。写形态：

- `off` 清除；与 `--secret-stdin` 同时给出退出 2。
- `auto` 自动查找；与 `--secret-stdin` 同时给出退出 2；找不到退出 1，并逐行列出每个候选的结果。
- 其它值按 §5.1.1 规范化，不合法退出 2。`--secret-stdin` 从标准输入读一行作为 secret，读到空行退出 2；不给就清掉 secret。secret 不接受命令行参数（`ps` 能看到命令行参数）。

执行顺序（照 multi-codex `cli.py:644-647`、`:1775-1789`）：① 先 `load_config` 一次，只用来判断要不要自动查找，用完丢弃；② 需要时做自动查找（最长约 30 s 加每个候选 3 s），不持锁；③ 查完后重新 `load_config`、按改完后的结果校验、修改、`_commit`。写锁在 `_commit` 里（`cli.py:780`），查找期间别的命令写入的内容会在第 ③ 步读到，不会被覆盖，校验也按最新配置做。

#### 5.1.6 自动查找（`clashfind.py`）

照搬 multi-codex `clashfind.py` 的 POSIX 部分：

1. `platform.list_process_argv()` 列出所有进程的参数：Linux 读 `/proc/<pid>/cmdline`，macOS 用 `ps -axww -o command=`（30 s 超时）。`ps` 输出不带引号，用 `split_ps_command` 近似还原含空格的路径。
2. 程序名含 `mihomo` / `clash`，或带 `-ext-ctl*` 参数的进程才处理。取 `-ext-ctl`、`-ext-ctl-unix`、`-secret`、`-d`、`-f`，读 `-f`（或 `-d` 下的 `config.yaml`）顶层的 `external-controller`、`external-controller-unix`、`secret`。
3. macOS 再读 Clash Verge 的 `~/Library/Application Support/io.github.clash-verge-rev.clash-verge-rev/config.yaml`。
4. 只接受本机地址（`0.0.0.0` 改成 `127.0.0.1`，`::` 改成 `::1`）。非本机地址跳过，读到的 secret 不往外发。
5. unix 套接字排在 TCP 前面。逐个 `GET /configs`，第一个返回 200 且含 `mode` 与 `tun.enable` 的就采用；unix 套接字保存时 secret 为空。
6. 结果说明只写地址与来源，不写 secret。`--verbose` 时逐行打印到 stderr。

测试注入：`MULTI_GROK_TEST_CLASH_PROCESSES=文件`，文件内容是参数列表的 JSON 数组，代替真实进程列表；`MULTI_GROK_TEST_CLASH_CONFIGS=路径1:路径2` 代替已知配置文件位置（测试不能读本机真实的 Clash Verge 配置）。

### 5.2 接口变更

| 类型 | 名称 | 变化 | 兼容性 |
| ---- | ---- | ---- | ---- |
| 子命令 | `netcheck`、`clash-api` | 新增 | 新增 |
| 隐藏子命令 | `netgate NAME` | 新增，只供检查版启动命令调用，不进帮助与补全 | 新增 |
| 退出码 | `5` | 新增：网络检查未通过，没有启动任何程序 | 旧退出码不变 |
| 环境变量 | `MULTI_GROK_SKIP_NETCHECK=1` | 新增：本次跳过 | 新增 |
| 配置 | 账号 `net_check`、顶层 `clash_api` | 新增，只在开启 / 设置时写出 | 0.3.0 读到会报 unknown keys 退出 1，CHANGELOG 写明 |
| 启动命令 | 开启检查的账号多出检查段 | 修改 | 未开启的逐字节不变 |
| Python 接口 | `launcher.render(..., *, net_check=False)` | 新增仅限关键字参数 | 旧调用不变 |

无 `docs/reference/*` 改动，不涉及 reference sibling 回补。

## 6. 备选方案与决策

1. **启动命令整体改由 Python 启动 grok**：否决。所有账号都会多一次 Python 启动，违背 “未开启账号逐字节不变”。只给开启的账号加一段子进程检查。
2. **trace 地址用 `grok.com` 或 `api.x.ai`**：否决。Clash 规则模式下，不同域名可能走不同出口；Grok 对话连的是 `cli-chat-proxy.grok.com`，查同一个域名才对得上。
3. **启动时也自动查找 Clash 接口**：否决。要枚举进程，每次启动都慢；接口只在设置时找。

## 7. 影响分析

正向：

- `config.py` 的 `Account` / `Config` 多了字段：所有 `copy()` 调用点（`cli.py` 的写命令、`accounts.plan`）都会带上新字段；`to_dict` 只在开启时写出，`accounts.plan` 用 `dump_config` 比较新旧内容（`accounts.py:85`），未开启账号不会产生配置变化。
- `render` 多了参数：调用点只有 `accounts.py:183` 与 `:283`（`grep -rn "render(" src` 全集，另一处 `routes.py` 是 `plan_router` 自己的渲染）。`launcher_status` 用同样参数生成期望内容，所以开启 / 关闭检查后 `list` 会显示 `launcher=stale`，`apply` 收敛。netcheck 写命令本身走 `_commit`，同一轮就会重写启动命令。
- `_prepare` / `_exec_grok` 多了可选参数：调用点 `cmd_run`、`cmd_code`、`cmd_mcp`、`cmd_usage`（`grep -n "_prepare(\|_exec_grok(" src/multi_grok/cli.py` 全集）。只有前两个传值，其余行为不变。
- `cli.run` 的 `netgate` 拦截在 argparse 之前，只匹配第一个参数正好是 `netgate`，没有子命令叫这个名字，不影响其它命令。

反向：

- `grok-auto` 路由脚本（`routes.py`）没改，但它 exec 的账号启动命令变了，所以路由到开启检查的账号时也会检查；路由到未开启的账号不变。
- `apply -f` 原来整份替换配置；现在 `clash_api` 例外，沿用本机值。
- `completion` 从 argparse 读子命令与选项（`completion.py:46-54`），新子命令自动进入补全；隐藏的 `netgate` 不在 argparse 里，不会出现。
- `doctor`、`list` 不读新字段，输出不变。

运行时：

- 未开启检查的账号：启动命令内容不变，没有新进程。
- 开启的账号：每次启动多一个 `multi-grok netgate` Python 子进程。基础档约等于一次 Python 启动加一次本机 TCP 连接；加强档另加两次 HTTPS 请求；Clash 检测一次本机 HTTP 请求。全部开启时最坏 3 s + 3 s + 8 s + 8 s = 22 s 后失败。
- 网络：加强档每次启动两次约 1 KB 的 GET，发往 `cli-chat-proxy.grok.com`。
- 自动查找：macOS 一次 `ps`，读至多几个配置文件，逐个候选最多 3 s。

## 8. 回归测试

全部在临时 `HOME` 下跑（`tests/helpers.py` 的 `isolated_home`），不碰真实 `~/.grok`、`~/.grok-accounts` 与本机 Clash。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| N1 | 未开启账号的 `render` 输出与不传 `net_check` 相同，且不含 `netgate`；`config.json` 不含 `net_check` / `clash_api` | 字符串相等 / 键不存在 |
| N2 | `netcheck home basic` 而代理是 `inherit` | 退出 2，配置不变 |
| N3 | `proxy home 7890` 后 `netcheck home strict --country jp` | 退出 0；配置里 `level=strict`、`country=JP`；启动命令含 `netgate home` |
| N4 | `netcheck home strict` 而代理是 `https://...` | 退出 2 |
| N5 | `--clash on` 与 `--clash-tun off` 同时给 | 退出 2 |
| N6 | 只开 `--clash-global on` | 退出 0，stderr 有 TUN 提醒 |
| N7 | 端口检查：本地监听的端口通过，关掉的端口失败并写 `connection refused` | `run_checks` 结果 |
| N8 | 国家检查：假 TLS trace 服务返回 `loc=US` 通过、`loc=JP` 失败、无 `loc` 失败、HTTP 500 失败 | 结果与说明 |
| N9 | 经代理：假 HTTP 代理处理 `CONNECT` 转到假 trace 服务，通过；代理返回 403 时说明含 `proxy refused CONNECT` | 结果与说明 |
| N10 | Clash：假 mihomo（TCP + secret）返回 `global` / `rule` / `direct`、TUN 开 / 关的组合，按 §5.1.2 判定；缺 secret 时 401 说明 | 每个组合的结果 |
| N11 | Clash unix 套接字形式的假 mihomo 能读到状态 | 通过 |
| N12 | 检查版启动命令端到端：PATH 里放假 `grok` 与指向源码的 `multi-grok`；检查失败时退出 5、假 grok 没运行；`MULTI_GROK_SKIP_NETCHECK=1` 时运行且 grok 环境里没有这个变量；通过时 stderr 有 `passed` | 退出码、标记文件、环境输出 |
| N13 | 检查版启动命令在 PATH 里找不到 `multi-grok` | 退出 127 |
| N14 | `run home -- 命令`、`login home` 在检查失败时退出 5 且命令没运行；未开启账号照常运行 | 退出码、标记文件 |
| N15 | `mcp home list`、`usage home SESSION` 在检查会失败的账号上照常运行 | 假 grok 被调用 |
| N15a | `run home --`（不带命令）、`code home` 在检查失败时退出 5，假 grok / 假 code 没运行 | 退出码、标记文件 |
| N15b | 路由到开启检查的账号时，`grok-auto` 退出 5；路由到未开启账号照常运行 | 退出码、标记文件 |
| N15c | 手改 `config.json` 开启检查后 `list` 显示 `launcher=stale`，`apply` 后恢复 `ok` 且启动命令含 `netgate` | 输出与文件内容 |
| N15d | 解析：`net_check` 含未知子键或 `level` 非法、`clash_api` 缺 `address` 时报 ConfigError；缺省子键按 §5.1.1 默认 | 异常与字段值 |
| N16 | `netcheck home` 只读形态逐项列出，有失败退出 5；未开启时打印 `off` 退出 0 | 输出与退出码 |
| N17 | `clash-api 127.0.0.1:9097 --secret-stdin` 保存 secret；`clash-api` 显示 `secret set` 不显示内容；`clash-api off` 清除；`clash-api a/b` 退出 2 | 配置与输出 |
| N18 | `clash-api auto`：测试进程列表含 `mihomo -ext-ctl 127.0.0.1:端口 -secret s`，假 mihomo 在该端口，保存成功；进程列表为空时退出 1 并列出说明 | 配置、退出码 |
| N19 | `netcheck home --clash on` 时接口未设置，测试进程列表能找到 → 一起保存；找不到 → 警告并照样写入；查找期间另一条命令改了代理（测试里替换查找函数，在其中改配置）→ 结果保留该改动 | 配置与 stderr |
| N20 | `apply -f` 文件含 `clash_api`：提醒已忽略，本机值不变 | 配置与 stderr |
| N21 | `split_ps_command` 拆含空格路径；`_yaml_scalar` 处理引号与注释 | 单元断言 |
| N22 | 原有 43 项测试全部通过 | `python3 -m unittest discover -s tests` |

## 9. 日志 / 观测点

- 放行：stderr `multi-grok: network check passed for account NAME (scope; N ms)`。
- 跳过：stderr `multi-grok: network check skipped for this run (MULTI_GROK_SKIP_NETCHECK=1)`。
- 拒绝：stderr `multi-grok: network check failed for account NAME (scope): 项: 原因 (N ms)`，退出码 5。
- 诊断：`multi-grok netcheck NAME` 每项一行 `ok|fail 项 说明 (N ms)`。
- 自动查找：stderr `[multi-grok] found Clash controller ADDRESS (mode=..., tun on|off)`，`--verbose` 时列出每个候选。
- 以上输出都不含 secret。
