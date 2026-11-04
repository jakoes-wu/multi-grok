# multi-grok：日常命令

> 2026-11-03 注记：与 v0.2.0 代码一起落地。配置版本仍是 1。本机安装在测试通过之后执行。提交与推送未做。

## 1. 背景

v0.1 给每个账号一个真实目录和 `grok-<名称>`。multi-claude 另外还有改设置、按目录选账号、列会话、交接会话、导出技能、补全脚本这些命令。

Grok 用户指南里能对上的部分：

- 会话按工作目录分组，目录是 `GROK_HOME/sessions/<编码后的工作目录>/<会话 id>/`，索引是 `summary.json`（`17-sessions.md`「Storage Layout」）。
- `grok --resume` 按当前目录匹配标题；`grok sessions list` 也只列当前目录（同文「From the Command Line」「The grok sessions Subcommand」）。
- 用量的官方入口是 `grok usage <session-id> [turn]`（同文「The grok usage Subcommand」）。
- 技能在 `$GROK_HOME/skills`。`SKILL.md` 本身可以是软链（`08-skills.md`「Additional Skill Directories」）。额外目录可以写在 `[skills] paths`、`plugins.paths`、`paths.extra_rule_dirs`（`26-config-reference.md`）。`[paths] extra_skill_dirs` 不注入技能。
- 沙箱启动拒绝符号链接形式的 `$GROK_HOME`（`18-sandbox.md`）。
- 状态栏载荷没有额度摘要（`25-status-line.md`「Available data」）。

## 2. 目标 / 非目标

**目标**

1. 补上能落到 `GROK_HOME`、会话目录或已有 `grok` 子命令的操作。
2. 共享条目用账号目录里面的软链。`GROK_HOME` 本身仍是真实目录。
3. 没有新字段的旧 `config.json`，再 dump 一次不出现 `shared` 和 `routes`。`doctor` 不因此报漂移。

**非目标**

- 不迁移 `~/.grok`，不把它换成软链，不复制 `auth.json`。
- 不做在线套餐、5 小时窗口、7 天额度，也不做 status line 配额回写。用户指南写明移植脚本来读 rate-limit summary 会读到空。
- 不读、不刷新 `auth.json`，不做钥匙串身份。
- 不做 `hook-latency`、`rename-mcp`、发布包校验和、`curl | sh`。
- 不断言 VS Code 扩展会读 `GROK_HOME`。`code` 只设置环境和独立的 `--user-data-dir`。
- 交接不改 `summary.json`。用户指南没有写「换一个 `GROK_HOME` 之后仍能 `--resume`」。
- 用户指南只写了 `SKILL.md` 可以是软链，没有写整个 `skills/` 目录是软链时的发现行为。本工具仍做目录软链，这一行为未在 Grok 里验证。

## 3. 假设与约束

- Python ≥ 3.8，只用标准库。配置 `version` 保持 1。
- 账号名 `auto`（大小写不敏感）留给 `grok-auto`。改名后留下的目录名仍可以是 `auto`。
- 共享默认关闭。JSON 里没有 `shared` 字段就是关闭。
- 可共享的名字只有 `skills`、`commands`、`agents`、`plugins`、`workflows`、`hooks`、`rules`、`personas`。默认三项是前三个。`sessions`、`memory-v2`、`logs`、`bin`、`bundled`、`auth.json`、`config.toml` 不在名单里，所以不会被链走或打进压缩包。
- 写命令仍是一把锁、先计划、有冲突就一条都不写。退出码不变：0 成功，1 错误，2 用法，3 冲突，4 锁被占用。

## 4. 涉及模块

| 区域 | 行号锚点 | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_grok/__init__.py` | `__version__` | 修改 | 版本改为 `0.2.0` |
| `src/multi_grok/config.py` | `Account.to_dict` 72、`Config.to_dict` 133、`parse_config` 185、`validate_name` 301 | 修改 | 解析并在缺省时省略 `shared`、`routes`、`dir` |
| `src/multi_grok/accounts.py` | `plan` 62、`_account_actions` 103、`_isolate_action` 145 | 修改 | 计划里加入共享链接和路由；被改过的受管 `config.toml` 不再写回 |
| `src/multi_grok/cli.py` | 子命令登记 99–199，`_commit` 554，各 `cmd_*` | 修改 | 新命令的入口 |
| `src/multi_grok/doctor.py` | `diagnose` 16、路由状态 46 | 修改 | 路由缺失、过期、冲突算问题 |
| `src/multi_grok/fsutil.py` | `unlink_link` 104、`same_target` 110 | 新增 | 只删除软链；比较链接目标时不再解析第二层软链 |
| `src/multi_grok/shared.py` | `plan_shared` 17、`plan_remove_links` 75 | 新增 | 共享软链的计划 |
| `src/multi_grok/routes.py` | `resolve` 44、`render_router` 63、`plan_router` 121 | 新增 | `grok-auto` |
| `src/multi_grok/sessions.py` | `list_account` 21、`handoff` 73 | 新增 | 只读 `summary.json`；交接复制目录 |
| `src/multi_grok/bundle.py` | `export_account` 26、`import_account` 50 | 新增 | 技能类目录的 tar.gz |
| `src/multi_grok/completion.py` | `render` 67 | 新增 | bash、zsh、fish 补全，不改 shell 启动文件 |
| `tests/test_more.py` | `MoreTest` | 新增 | 新命令的回归 |
| `tests/test_config.py` | 未知键与缺省 dump | 修改 | 顶层 `shared` 已是合法键 |

## 5. 方案

### 5.1 实现要点

**配置兼容。** `Account.to_dict`（`config.py:72`）在 `shared` 为假、排除列表和 `managed_links` 为空、`dir` 等于账号名时不写这些键。`Config.to_dict`（`config.py:133`）在没有共享目录且条目是默认三项时不写 `shared`，在没有规则也没有默认账号时不写 `routes`。`parse_config`（`config.py:192`）允许这两个顶层键，其它未知键仍拒绝。

**计划顺序。** `plan`（`accounts.py:62`）先让 `plan_shared` 改内存里的 `managed_links`，再生成 `config.json` 动作，执行时仍把写配置放在列表第一项（`accounts.py:80`）。`_commit`（`cli.py:554`）在锁内重新读盘上的旧配置，把旧的 `shared.dir` 传给计划，用来识别「链接还指向换掉之前的共享根」。

**受管 config.toml。** 标记还在、正文已经不等于模板时，`_isolate_action`（`accounts.py:145`）改为 `skip`，不再 `update`，`--no-isolate` 也不删除。Grok 会在标记还在的文件末尾追加段落；按标记整份写回会清掉那些段落。正文仍等于模板时，行为和 v0.1 一样。

**共享软链。** 默认关。`add` / `set --shared [DIR]` 打开；`--shared DIR` 同时设置全局共享目录；只写 `--shared` 且还没有目录时用 `~/.grok-shared`（`cli.py` `_apply_share_flags`）。源目录不存在就跳过，不打印 `skip`。已经指向正确目标的用户软链不记入 `managed_links`，除非该次带了 `--adopt`（`shared.py:43`）。真实目录或指向别处的链接是冲突，退出码 3，什么都不写。`remove` 先删仍指向共享目录的受管软链，再去掉指向该账号的路由，然后注销。账号目录不删。

**路由。** `grok-auto` 第 2 行是 `# managed-by: multi-grok router`（`routes.py:18`）。启动命令的标记是 `account=`，孤儿清理不会把路由脚本当成某个账号的启动命令删掉。`resolve`（`routes.py:44`）用物理路径，最长的可进入祖先赢；长度相同保留先写下的规则；进不去的目录跳过。没有命中也没有默认账号时执行 `PATH` 里的 `grok`。路由关掉且文件是本工具的路由脚本时才删除；别人的 `grok-auto` 在路由启用时是冲突。

**会话。** `sessions` 只打开 `summary.json`（`sessions.py:21`）。标题顺序是手工标题、`generated_title`、`session_summary`，空白折叠后最多 80 字。不打印 `grok_home`。`--cmd` 打印 `cd <工作目录> && grok-<名称> --resume <id>`，因为按标题恢复和 `grok sessions list` 都看当前目录。`usage` 把参数转给 `grok usage`，不读 `signals.json`（`cli.py` `cmd_usage`）。

**交接。** 会话 id 只能是单层目录名。同一个 id 出现在多个工作目录分组里就拒绝猜测。复制只收普通文件和真实目录（`sessions.py` `_copy_regular_tree`）。先写到 `<目标>.partial`（`sessions.py:92`），成功后再换上；`--force` 先把旧目录改名为 `.bak`，换上失败再改回去。失败时不删原来的会话。两端账号目录都必须已经存在。源账号省略时用 `grok-auto` 对当前目录的选择。同一账号是用法错误。

**导出与导入。** 格式 `multi-grok-bundle` 版本 1。只打包上述白名单里的真实目录，软链跳过，避免把共享目录整棵拷走。压缩包权限 `0600`。拒绝绝对路径、`..`、反斜杠、NUL、软链和设备文件。导入进已有账号。目标已是共享软链时，即使 `--force` 也拒绝。已有真实目录没有 `--force` 时拒绝；有 `--force` 时先改名为 `.bak` 或 `.bak.N`。

**其它命令。** `set` 改已有账号的代理、隔离和共享。`rename` 改配置键和启动命令，目录与登录文件留在 `dir`。`path` 打印目录，目录不存在则退出码 1。`run` 必须带 `--`；`--` 后面为空时执行 `grok` 加固定参数；非空时按原样执行，不附加固定参数。`mcp` 在账号名之后的参数原样交给 `grok mcp`，不经过会吃掉 `--json` 的解析器（`cli.py` `_split_forwarded`）。`which` 只打印 `resolve` 的结果。`completion` 打印脚本，不改 `~/.zshrc`。`code` 在找到 `code` 之后才创建 `~/.grok-accounts/.apps/<目录名>/vscode`，并打印实验性警告。

### 5.2 接口变更

新增命令：`set`、`route`、`which`、`rename`、`run`、`path`、`mcp`、`usage`、`sessions`、`handoff`、`code`、`export`、`import`、`completion`。

`list` 增加 `--json`、`--names`，文本行增加 `shared=on|off`。有路由时多一行 `router=`。`doctor` 增加 `--json`。`init` 增加 `--shared-dir`、`--shared-items`。`add` / `set` 增加 `--shared`、`--no-shared`、`--shared-exclude`、`--shared-include`，`add` 另有 `--adopt`。

`config.json` 新增可选对象 `shared`（`dir`、`items`）和 `routes`（`default`、`rules`）。账号对象新增可选 `shared`、`shared_exclude`、`managed_links`、`dir`。缺省不写出。旧文件仍能解析。

压缩包是新文件格式，不是 Grok 的配置。

无 `docs/reference/` 改动。reference sibling 回补检查：N/A。

## 6. 备选方案与决策

把共享目录写进 `[skills] paths`：否决。Grok 会改 `config.toml`，而正文一旦偏离模板，本工具就不再写回（`accounts.py` `_isolate_action`）。软链不依赖这份文件。

从 `signals.json` 汇总用量：否决。用户指南指定 `grok usage`。

交接时改写 `summary.json` 里的路径：否决。用户指南的索引字段没有要求这种改写，改了反而可能和 `updates.jsonl` 不一致。

把 `~/.grok` 收成默认账号：仍否决。理由与 v0.1 相同，沙箱拒绝软链形式的 `GROK_HOME`。

## 7. 影响分析

**正向。** `plan` 被 `_commit`（`cli.py:557`）和 `diagnose`（`doctor.py:33`）调用。共享和路由的动作因此进入每一次写命令和 `doctor`。`doctor` 不保存。`plan_shared` 会改内存里的 `managed_links`；缺省账号的这份列表仍为空，`to_dict` 不写出，所以未启用共享时 dump 与磁盘一致。

`remove`（`cli.py:369`）在计划之前插入删链接动作，并删掉指向该账号的规则和默认账号。账号目录、`auth.json`、会话仍留在磁盘。

`rename`（`cli.py:700`）改账号名和路由里的名字，不改 `account.dir`。启动命令按新名字重建，旧的 `grok-<旧名>` 由孤儿清理删除。目录里的 `auth.json` 不动。

`grok-auto` 最后 `exec` 对应的 `grok-<名称>`，隔离变量仍由那份启动命令清除。没有命中时 `exec grok`，这时用的是调用者 shell 里的环境，包括 `~/.grok`。

**反向。** 同一把写锁（`lock.py` `WriteLock`）。`handoff` 只拿锁复制会话，不写 `config.json`。`sessions`、`which`、`path`、`completion` 不加锁。

v0.1 的启动命令文本在代理、环境变量、固定参数没变时不变。`list` 多了 `shared=` 字段，旧测试按子串比较，仍然能过。

**运行时。** 不新增常驻进程。`login`、`usage`、`mcp`、`run`、`code` 各启动一次子进程。会话列表只读 `summary.json`，不打开 `updates.jsonl` 和 `chat_history.jsonl`。交接在复制完成前多占一份会话目录的磁盘。压缩包写在目标文件旁边，完成后 `os.replace`。

**部署。** 已有的 `~/.config/multi-grok/config.json` 没有新键。安装新版本本身不跑 `apply`。本机账号 `home` 的 `config.toml` 首行仍是受管标记，正文已被 Grok 追加 `[marketplace]`。按本节的 `_isolate_action`，之后的 `apply` 留下这份文件。v0.1 的行为是写回模板。

**对外语义。** `grok-<名称>` 的环境变量约定不变。新脚本只有 `grok-auto`。

## 8. 回归测试

```sh
python3 -m unittest discover -s tests -v
```

覆盖：

- 缺省 dump 不出现 `shared`、`routes`；未知键 `identity` 仍拒绝。
- 账号名 `auto` 拒绝。`rename` 后目录和 `auth.json` 还在，启动命令换成新名字。
- 路由选择最长目录，`grok-auto` 通过 `sh -n`。
- `sessions` 输出不含种在 `updates.jsonl` 里的字符串。
- 交接能复制 `summary.json`；遇到软链失败，且失败前已经复制的目标文件还在，不留下 `.partial`。
- `--shared` 生成软链；目标已是真实目录时退出码 3，目录不被替换。
- `path` / `run` 使用该账号的 `GROK_HOME`。
- 导出不含 `auth.json`，文件权限不含 group/other 位。
- 补全脚本含 `route`。
- 受管 `config.toml` 被追加段落之后，`apply` 和 `set --no-isolate` 都不改文件。
- v0.1 的添加、删除、代理、安装幂等仍然通过。

本机安装后用 `multi-grok` 和 `multi-grok doctor` 做只读核对。不跑 `login`，不对真实配置执行 `apply`。

## 9. 日志 / 观测点

- 写命令的行首是 `[multi-grok]`，后接 `create`、`update`、`delete`、`skip` 或 `conflict`。
- 受管文件被留下时原因是 `managed file was edited after it was written; left untouched`。
- `multi-grok` 文本行含 `shared=off` 或 `shared=on`。启用路由后有 `router=ok|missing|stale|conflict`。
- `multi-grok doctor --json` 的 `ok` 为假时退出码 1。
- `multi-grok list --json` 的 `accounts` 不含 `auth.json` 内容。
