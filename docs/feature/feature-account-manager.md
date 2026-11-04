# multi-grok：Grok 多账号目录与启动命令

> 2026-11-03 注记：用户要求参照 multi-claude，在 `/Users/jakoes/VSCodeProjects/ai/multi-grok` 做成开源项目。账号目录为 `$HOME/.grok-accounts/<账号名>`，启动命令为 `grok-<账号名>`（例：`grok-home`）。本文与 v0.1.0 代码一起落地。
>
> 2026-11-03 补充：v0.2 增加了当时列为非目标的共享软链、目录路由、会话列表与交接、`grok usage` 转发。那些能力的边界在 `docs/feature/feature-everyday-commands.md`。下文第 2 节的非目标是 v0.1 的范围。v0.3 的本地报表在 `docs/feature/feature-local-reports.md`。`--help` 的分组和示例在 `docs/feature/feature-help-examples.md`。

## 1. 背景

Grok 的数据目录由 `GROK_HOME` 决定，未设置时是 `~/.grok`（用户指南 `05-configuration.md` 的 Paths 表、`17-sessions.md` 开头、`14-headless-mode.md` 的 File Locations）。同一份 `~/.grok/auth.json` 里只有一个登录；`grok login` 会换掉它（用户指南 `02-authentication.md`）。

要在本机同时开几个账号，需要各自的目录和启动入口。手工维护容易把 `XAI_API_KEY`、`GROK_AUTH_PATH` 或兼容扫描混进别的账号。

multi-claude 用「一份 config.json + 每账号一个启动脚本」做这件事。本项目沿用这个收敛方式，换成 Grok 的 `GROK_HOME`。不搬 multi-claude 里和 Claude 绑死的部分：钥匙串、用量接口、状态栏钩子、VS Code 启动、会话交接、目录路由。

## 2. 目标 / 非目标

**目标**

1. 每个账号一个真实目录，默认 `$HOME/.grok-accounts/<账号名>`。
2. 每个账号一个启动命令 `grok-<账号名>`，把 `GROK_HOME` 指到该目录。
3. 写命令可重复执行；冲突时一条都不写；`remove` 不删除账号目录。

**非目标**

- 不迁移 `~/.grok`，不把它换成软链。直接运行 `grok` 的行为不变。
- 不读取、不复制、不打印 `auth.json` 的内容。
- 不修改 shell 启动文件。
- 不删除账号目录，也不提供递归删目录的工具函数。
- 不做共享软链。Grok 是否跟随 `GROK_HOME` 内部的软链加载 skills，用户指南没有写。
- 不联网，不查询套餐。
- 不支持 Windows。
- 不实现 multi-claude 的 statusline、usage、会话交接和目录路由。

## 3. 假设与约束

- Python ≥ 3.8，只用标准库。macOS 与 Linux。调用的是 `PATH` 里的 `grok`。
- 账号名：`^[A-Za-z0-9][A-Za-z0-9._@+-]{0,63}$`。`home` 只是一个合法名字，不是默认身份。大小写不同视为同一个账号（macOS 默认文件系统不区分大小写）。
- 默认路径：

| 项目 | 路径 |
| ---- | ---- |
| 账号根目录 | `~/.grok-accounts` |
| 启动命令目录 | `~/.local/bin` |
| 工具状态 | `$XDG_CONFIG_HOME/multi-grok/`，未设置时 `~/.config/multi-grok/` |
| 安装位置 | `~/.local/share/multi-grok` |

- Grok 1.0.70 用户指南里用到的事实：
  - `GROK_HOME` 覆盖数据目录。符号链接形式的 `GROK_HOME` 在沙箱启动时被拒绝（`18-sandbox.md`）。
  - 已有会话令牌时，令牌优先于 `XAI_API_KEY`；没有会话令牌时才用这把钥匙（`02-authentication.md`）。`GROK_CODE_XAI_API_KEY` 是同一类钥匙（`11-custom-models.md`）。
  - `GROK_AUTH_PATH` 与 `GROK_AUTH` 可以提供账号目录以外的登录身份（`26-config-reference.md` 实验分桶那段）。
  - `GROK_CONFIG` 会盖过账号自己的 `config.toml` 里允许覆盖的设置（`05-configuration.md`）。它改不了鉴权策略，但会让各账号共用同一份模型设置。
  - `[compat.claude]` 与 `[compat.cursor]` 默认全开，扫描 `~/.claude` 与 `~/.cursor`，不随 `GROK_HOME` 改变（`05-configuration.md` Harness compatibility）。
- 启动命令是 `0755` 明文脚本。秘密不能写进去。
- 账号目录不能等于 `~/.grok`，不能位于其下，也不能把它包在里面。

## 4. 涉及模块

仓库新建。下表是 v0.1.0 的全部代码。

| 区域 | 文件 | 行号锚点 | 改动 |
| ---- | ---- | ---- | ---- |
| 文件工具 | `src/multi_grok/fsutil.py` | `unlink_file` | 新增。只删普通文件，不递归删目录 |
| 锁 | `src/multi_grok/lock.py` | `WriteLock` | 新增 |
| 动作 | `src/multi_grok/actions.py` | `Action` | 新增。日志前缀 `[multi-grok]` |
| 路径 | `src/multi_grok/platform.py` | `default_root`、`ISOLATION_ENV` | 新增 |
| 配置 | `src/multi_grok/config.py` | `parse_config`、`normalize_proxy`、`validate_env_key` | 新增 |
| 启动命令 | `src/multi_grok/launcher.py` | `render`、`build_env` | 新增 |
| 收敛 | `src/multi_grok/accounts.py` | `plan`、`isolate_text`、`describe` | 新增 |
| 检查 | `src/multi_grok/doctor.py` | `diagnose` | 新增 |
| 命令 | `src/multi_grok/cli.py` | `run`、`_commit`、`cmd_login` | 新增 |
| 安装 | `install.sh` | `usage` | 新增 |
| 测试 | `tests/test_*.py` | 各 `TestCase` | 新增。临时 `HOME`，不碰真实家目录 |

## 5. 方案

### 5.1 实现要点

`config.json` 是唯一权威源。写命令的顺序是：加锁 → `plan` 只读算出动作 → 有 `conflict` 则什么都不写并返回 3 → 否则按顺序执行。`--dry-run` 只打印。

`plan`（`accounts.py`）为每个账号生成三类动作：

1. 账号目录。缺失则创建并 `chmod 0700`。已是真实目录则不动，不改已有权限。软链或普通文件是冲突。
2. `config.toml`。`isolate` 为真且文件不存在时，写入带 `# managed-by: multi-grok account=<名称>` 首行的文件，关掉 Claude / Cursor 兼容扫描。首行标记匹配才允许更新或删除。用户自己的文件留下，记为 `skip`。软链是冲突，不跟随。
3. `grok-<名称>`。第 2 行必须是受管标记。没有标记的同名文件是冲突。内容相同则不变。

不再登记的账号：只删除带本工具标记、且文件名正好是 `grok-<名称>` 的启动命令。

启动脚本（`launcher.py` `render`）：

- 主目录下的账号写成 `account_dir="$HOME/.grok-accounts/<名称>"`，不把某一次展开的绝对路径写死。
- 先拒绝软链，再检查目录存在，然后 `export GROK_HOME`。
- `unset` `platform.ISOLATION_ENV` 里的变量。
- 按代理值设置或清除代理变量。
- `exec grok <固定参数> "$@"`。找不到 `grok` 时退出 127。

`login` 不读 `auth.json`。它用 `build_env` 组出与脚本相同的环境，执行 `grok login`。`list` 只用 `lstat` 看 `auth.json` 是普通文件、软链还是缺失。

退出码：`0` 成功，`1` 错误，`2` 用法，`3` 冲突，`4` 锁被占用。

### 5.2 接口变更

本仓库是新的。对外命令见 README。配置文件字段：

| 字段 | 含义 |
| ---- | ---- |
| `version` | 只接受 `1` |
| `root` | 账号根目录，默认 `~/.grok-accounts` |
| `bin_dir` | 启动命令目录，默认 `~/.local/bin` |
| `defaults.env` / `defaults.args` | 所有账号的默认环境变量和固定参数 |
| `accounts.<名称>.proxy` | `inherit`、`off`、端口或 `http(s)` URL |
| `accounts.<名称>.isolate` | 是否维护那份兼容扫描开关，默认 `true` |
| `accounts.<名称>.env` / `args` | 账号自己的环境变量和固定参数 |

未知字段拒绝，避免把 multi-claude 的 `shared`、`identity` 静默丢掉。

无 reference 文档。sibling 回补：不适用。

## 6. 备选方案与决策

把 `~/.grok` 收成一个名为 `home` 的默认账号：否决。沙箱拒绝符号链接形式的 `GROK_HOME`，而「目录搬走、原处留软链」会让直接运行的 `grok` 碰到软链。用户要的 `grok-home` 是账号名 `home`，不是把默认目录改名。

只写 shell 别名、不做 config.json：否决。别名改不了幂等和冲突检测，卸掉工具后也不如独立脚本稳。

## 7. 影响分析

- 本工具新建的路径只有：状态目录、账号根目录、账号子目录、受管 `config.toml`、受管启动命令。
- `~/.grok`、shell 启动文件、已存在且没有受管标记的 `config.toml`、没有受管标记的 `grok-*` 文件，都不改。
- `remove` 与 `apply -f` 删掉的是受管启动命令。账号目录留着，所以登录和会话还在。
- 父进程里的 `XAI_API_KEY` 等变量对 `grok-<名称>` 不生效，对直接运行的 `grok` 仍然生效。
- 机器级 `/etc/grok` 与 MDM 不在本工具范围内，各账号仍然一起吃到。
- 仓库里的 `.grok/` 是项目级配置，谁打开同一个仓库谁共用。本工具不拆这一层。

## 8. 回归测试

在临时 `HOME` 里跑 `python3 -m unittest discover -s tests -v`。用例覆盖：

- `add home` 创建 `0700` 目录、`grok-home` 和隔离用的 `config.toml`；再跑一次没有 `create`。
- 启动脚本把 `GROK_HOME` 设成 `$HOME/.grok-accounts/home`，并清掉父进程的 `XAI_API_KEY`、`GROK_AUTH`、`GROK_CONFIG`。
- 账号目录是软链时脚本退出 1，且不执行 `grok`。
- 没有受管标记的 `grok-home` 使 `add` 返回 3，配置和账号目录都不创建。
- 用户自己的 `config.toml` 内容不变。
- 根目录设为 `~/.grok` 时 `add` 返回 3。
- `remove` 删启动命令，留下含秘密字符串的 `auth.json`；`list` 与 `doctor` 的输出里没有这串秘密。
- `login` 调用的假 `grok` 看到该账号的 `GROK_HOME`，看不到 `XAI_API_KEY`，参数是 `login --device-auth`。
- `--dry-run` 不写盘。`apply -f` 可以加上再注销账号，目录仍在。
- `install.sh -h`、安装、重复安装、卸载。

## 9. 日志 / 观测点

- 写命令的每一条动作：`[multi-grok] <status> <kind> <path>`。冲突打到 stderr。
- `doctor` 的问题以 `[multi-grok] error:` 打出，退出码 1。警告以 `[multi-grok] warning:` 打出，不改变退出码。
- 没有任何结构问题时 stdout 有 `[multi-grok] ok`。

## 10. 开放问题

无。发布到 GitHub 与打 tag 不在本方案内，等仓库维护者决定。
