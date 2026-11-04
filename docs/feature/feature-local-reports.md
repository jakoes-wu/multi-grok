# multi-grok：本地会话报表

> 2026-11-03 注记：方案与 v0.3.0 代码同一轮落地。配置版本仍是 1。最近使用和用量历史都是读出来的，不写入 `config.json`。`python3 -m unittest discover -s tests` 41 项通过。本机已执行不带 `--config` 的 `./install.sh`，`multi-grok --version` 为 0.3.0。随 v0.3.0 发布到 `main`。

## 1. 背景

v0.2 的 `sessions` 只遍历 `config.json` 里的账号（`cli.py` `cmd_sessions`）。直接运行的 `grok` 把会话放在 `~/.grok`，这份列表里看不到。

`list` 没有最近使用时间。`summary.json` 的 `updated_at` 已经在会话列表里读过（`sessions.py` `_read_summary`）。

用量的官方入口是 `grok usage <session-id> [turn]`（用户指南 `17-sessions.md`「The grok usage Subcommand」）。输出是 JSON，顶层有 `sessionId`、`updatedAt`、`session`、`turns`。`costUsdTicks` 是每美元 `10^10` 个刻度。交互里的额度仍走 TUI `/usage`。`grok usage --help`（grok 1.0.70）只有 `SESSION_ID` 和可选的 `TURN`，没有工作目录参数。

`doctor` 只检查 `bin_dir` 是否出现在 `PATH` 的某一段（`doctor.py` `diagnose`）。PATH 更前面的另一个 `grok-<名称>` 仍会挡住受管启动器。

## 2. 目标 / 非目标

**目标**

1. 不带账号名的 `sessions` 把 `~/.grok` 里的会话标成 `default`，只要这个名字和这个目录都还没被已登记账号占上。
2. `list` 为每个已登记账号显示最近一次 `summary.json` 的 `updated_at`。
3. `usage <名称> --history` 对窗口内每个会话调用一次 `grok usage <id>`，只加总 JSON 里的数字，按日或按模型输出。

**非目标**

- 不把 `~/.grok` 登记进 `config.json`，不迁移它，不把它换成软链，不复制 `auth.json`。
- 不读 `auth.json`、`signals.json`、`usage.json`、`updates.jsonl`、`chat_history.jsonl`。
- 不查询 5 小时或 7 天在线额度，不做 status line 配额。
- 不实现断开的共享软链检查，也不改现有的那一行通用 `export PATH=...` 提示。
- 不改 `config.json` 的字段。`last` 和历史都是现算的。

## 3. 假设与约束

- 账号名 `default` 仍允许登记。它不是保留字。`auto` 继续保留给 `grok-auto`。
- `~/.grok` 本身如果是软链，不进入会话列表。沙箱拒绝软链形式的 `GROK_HOME`（用户指南 `18-sandbox.md`）。这里只做只读列表，也不顺着软链往下走。
- `grok usage` 是否只查找当前工作目录里的会话，用户指南和 `--help` 都没有写。本命令不改变进程的工作目录，只设置该账号的 `GROK_HOME`，并在输出里说明这一点尚未验证。
- 数字字段按 grok 1.0.70 二进制里的用量结构取 camelCase：`inputTokens`、`outputTokens`、`cacheCreationTokens`、`reasoningTokens`、`totalTokens`、`modelCalls`、`costUsdTicks`。状态栏文档里的 snake_case 名称不拿来解析这份 JSON。
- `modelUsage` 的每个模型对象里有哪些字段，没有对真实会话跑过 `grok usage`。只在值是对象时摘上述数字。没有 `modelUsage` 时，按 `session.primaryModelId` 把 `session` 上的数字放进一组。
- 按日汇总时，只要任一 `turns[]` 元素带有上述数字，就只用轮次上的数字，按 `endedAt`（没有则 `updatedAt`）的 UTC 日期分桶，不再加一遍 `session` 合计。轮次上没有这些数字时，把 `session` 合计放进 `summary.json` 的 `updated_at` 那一天。
- 时间用 UTC。Python 3.8 的 `fromisoformat` 不接受结尾的 `Z`，也不接受超过 6 位的小数秒；解析前先归一。解析不了的 `updated_at` 不参与“最近”的时间比较；`--days` 大于 0 时，这种会话不进入历史。

## 4. 涉及模块

| 区域 | 行号锚点（改前） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_grok/__init__.py` | `__version__` 第 3 行 | 修改 | `0.3.0` |
| `src/multi_grok/sessions.py` | `list_account` 第 21 行；`updated` 第 158 行 | 修改 | 增加时间解析和 `latest_updated`。仍只打开 `summary.json` |
| `src/multi_grok/usage_report.py` | 新文件 | 新增 | 筛选、去重、从 JSON 摘数字、加总 |
| `src/multi_grok/cli.py` | `usage` 解析器第 167–170 行；`cmd_list` 第 500–502 行；`cmd_usage` 第 765–773 行；`cmd_sessions` 第 776–797 行 | 修改 | `--history`；`last` 列；`default` 行 |
| `src/multi_grok/accounts.py` | `describe` 第 249–264 行 | 修改 | 增加 `last`。不写配置 |
| `src/multi_grok/doctor.py` | `diagnose` 账号循环第 54–76 行 | 修改 | PATH 遮挡只进 warnings |
| `tests/test_reports.py` | 新文件 | 新增 | 四项行为 |
| `tests/test_install.py` | 版本断言第 24 行 | 修改 | `0.3.0` |
| `README.md`、`README.zh-CN.md`、`CHANGELOG.md` | 命令表与文首 | 修改 | 0.3.0 说明 |
| `docs/feature/feature-everyday-commands.md` | 文首注记 | 修改 | 指向本文 |
| `docs/feature/feature-account-manager.md` | 文首补充注记 | 修改 | 指向本文 |

`completion.py` 不改。补全从 argparse 收集选项，`--history`、`--days`、`--by` 会跟着出现。

## 5. 方案

### 5.1 实现要点

**default 会话。** 不带名字的 `sessions` 在已登记账号之外再看 `platform.default_grok_home()`（`~/.grok`）。下面任一成立就不再追加：

- 已有账号的名字 casefold 后是 `default`（`Config.find`）。
- 某个已登记账号目录的绝对路径（`expand`，不解析软链）等于 `~/.grok`。

`~/.grok` 不存在则跳过，不提示。它是软链或其它非目录时，列出全部账号才给一条警告并跳过。用户写了 `sessions default` 且没有同名已登记账号时，不是目录就退出码 1。是目录则只读它的 `summary.json`，账号名打印成 `default`。`--cmd` 对这一行使用 `grok --resume`，不使用 `grok-default`。已登记账号仍用 `grok-<名称> --resume`。

**最近使用。** `describe` 对每个已登记账号调用已有的 `list_account`，用 `latest_updated` 取最大的 `updated_at`。能解析的时间按 UTC 比较，返回原来的字符串。一条都解析不了时，才退回非空字符串的字典序最大者。没有会话时是空字符串。文本列打印 `last=-`。`--json` 保留空字符串。`--names` 仍只打印名字。`doctor` 的 JSON 因为复用 `describe`，会带上同一个字段。不把 `~/.grok` 加进 `list`。

**用量历史。** `usage [<名称>] --history [--days N] [--by day|model] [--json]`。写了名称就只算这个账号。名称是 `default` 且没有同名已登记账号时，目录是 `~/.grok`，代理按 inherit，不附带任何账号的固定参数。省略名称时，已登记账号都算，并按上面的规则加上 `~/.grok`。`--days` 默认 7。`0` 表示不按时间截断。负数是用法错误，退出码 2。同时给了会话 id、轮次，或 `--` 后面的参数，也是用法错误。不带 `--history` 时，`--days`、`--by`、`--json` 是用法错误；原来的 `usage <名称> <会话 id> [轮次]` 仍原样转发给 `grok`，并且仍不附带账号的固定参数。

历史路径：`list_account` 取会话，按 id 去重（同一 id 出现在多个工作目录分组时只调用一次，留下 `updated_at` 较新的一条，并警告）。`--days` 大于 0 时丢掉没有可用时间的会话，个数放进 `skippedUndated`。对其余每个 id 执行 `grok usage <id>`，环境与 `login` 相同（`build_env`，清掉那六个隔离变量），不改工作目录，不附带固定参数。子进程的标准错误丢弃。退出码不是 0，或标准输出不是 UTF-8 JSON 对象时，对每个失败的 id 打印一行“id + 退出码或不是 JSON”，不打印原始输出，不打印加总表，退出码 1。

成功时按第 3 节的规则加总。`costIsPartial` 或 `usageIsIncomplete` 为 JSON 布尔值 `true` 时，结果标 `partial`，仍打印合计，并给警告。美元字符串由 `costUsdTicks` 整除 `10^10` 得到，不用浮点。文本和 `--json` 都带一行说明：工作目录未改，`grok usage` 是否受当前目录限制尚未验证。这条说明走标准错误，`--json` 的标准输出保持一份 JSON。

**PATH 遮挡。** 对每个已登记账号，`shutil.which("grok-" + name)` 的 `realpath` 与受管启动器路径的 `realpath` 不同，就追加一条 warning：`{found} comes first on PATH and hides {expected}`。`which` 为 `None` 时不追加，仍由已有的“bin 目录不在 PATH”问题负责。warning 不把 `doctor` 的退出码从 0 改成 1。不删除那个更靠前的文件。

### 5.2 接口变更

命令（新增旗标，原有调用方式保留）：

- `sessions [<名称>]`：名称可以是尚未登记的 `default`，表示只读 `~/.grok`。
- `list` 文本增加 `last=<时间或 ->`。`--json` 的每个账号对象增加字符串字段 `last`，没有会话时为 `""`。
- `usage [<名称>] --history [--days N] [--by day|model] [--json]`。省略名称时每个账号一份合计；只有一份时 `--json` 仍是单个对象，多份时是数组。
- `doctor` 可能多一条 warning，退出码规则不变。

`usage --history` 的 JSON 对象字段：`account`、`by`、`days`、`sessions`、`skippedUndated`、`partial`、`cwdUnverified`（恒为 `true`）、`rows`、`total`。`rows` 和 `total` 含 `key` 以及第 3 节列出的数字字段，外加字符串 `costUsd`。`rows[].key` 在 `--by day` 时是 `YYYY-MM-DD` 或 `(undated)`，在 `--by model` 时是模型名或 `(unknown)`。

`config.json` 无新字段。`to_dict` 不改。

无 `docs/reference/` 改动。reference sibling 回补检查：N/A。

## 6. 备选方案与决策

把 `~/.grok` 登记成账号再列会话：否决。登记会写启动器或要求 `GROK_HOME` 指向它，和“不迁移 `~/.grok`”冲突。

按日汇总时直接读 `usage.json`：否决。用户指南写明用 `grok usage`，不要读会话文件。

历史命令失败时打印 `grok usage` 的原文：否决。`turns` 里可以有对话文本。失败只报 id 和退出码。

PATH 遮挡算 problem、退出码 1：否决。挡住的是另一个程序，本工具没有写它的权限；和“没有 auth.json”一样只警告。

## 7. 影响分析

**正向。** `describe` 被 `cmd_list`（`cli.py` 第 488 行）和 `cmd_doctor`（`cli.py` 第 532 行）调用。两边都会多读每个已登记账号的 `summary.json`。`doctor` 的问题列表不因 `last` 变化。`cmd_sessions` 多读的只有 `~/.grok/sessions/**/summary.json`。`cmd_usage` 在 `--history` 时对每个入选 id 启动一次 `grok`，不写账号目录。

**反向。** 写命令仍走 `_commit`，本方案不改 `plan`。`config.json` 字节内容不应变化，因此已有的“文件系统与配置不一致”检查不会因为新字段触发。`handoff`、`export` 不读 `last`。补全脚本多出三个选项，位置参数种类不变。

**运行时。** 不新增常驻进程。`list` 和 `doctor` 的耗时随会话个数增加，只读小 JSON。`usage --history` 的子进程数等于去重后的入选会话数，没有另行封顶。标准错误被丢弃，标准输出只在内存里解析后丢弃原文。固定参数仍不进入 `usage` 和 `login`。

**部署。** 已安装的 `~/.local/bin/multi-grok` 是一份源码快照。测试通过后重新运行不带 `--config` 的 `./install.sh`，才会换成 0.3.0。安装不改 `grok-home` 和账号目录。

## 8. 回归测试

- 临时 `HOME` 里的 `~/.grok` 有一条 `summary.json`，旁边的 `updates.jsonl` 含秘密字符串。`sessions` 打印 `default` 和 `grok --resume`，输出不含该字符串。
- 已登记账号名叫 `default` 时，不再追加一份 `~/.grok`。
- `~/.grok` 是软链时，列出全部账号的输出不含链后的标题。
- `list` 的 `last` 等于较新的 `updated_at`。`--names` 不含这个时间。没有会话时文本是 `last=-`。
- `usage --history` 的假 `grok` 只应看到参数 `usage <id>`，`GROK_HOME` 是账号目录，工作目录不变，`XAI_API_KEY` 不在环境里。输出含加总数字，不含轮次里的秘密、不含 `signals.json` / `usage.json` / `updates.jsonl` 里的秘密。
- 假 `grok` 退出码非 0 时，退出码 1，且不出现它打到标准输出的秘密。
- 不带 `--history` 的 `usage <名称> <id>` 仍把 `grok` 的标准输出交给用户，且不附带固定参数。
- PATH 前面另有一个 `grok-home` 时，`doctor` 退出码 0，警告里有 `hides`，受管启动器还在。没有遮挡时警告里没有 `hides`。
- 已有“不打印 auth.json 正文”“受管 `config.toml` 被改过就留下”的用例保持通过。

## 9. 日志 / 观测点

- 成功的 `doctor` 仍打印 `[multi-grok] ok`。遮挡警告是 `[multi-grok] warning:`，里面有 `comes first on PATH and hides`。
- `usage --history` 的工作目录说明是 `[multi-grok] warning:`，里面有 `working directory was not changed`。
- 子进程失败是 `[multi-grok] error:`，里面有 `grok usage`、会话 id 和 `exited <code>`，或者 `did not return a JSON object`。
- 不新写日志文件。

## 10. 开放问题

无。`grok usage` 的工作目录范围是已记录的未验证事实，不是待选实现。
