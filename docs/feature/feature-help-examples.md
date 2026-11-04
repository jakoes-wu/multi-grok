# multi-grok：帮助里列出示例

> 2026-11-04 注记：用户要求参照 multi-claude 的 `--help`，为每条命令和子命令增加 Examples。本文与代码同一轮落地。只改帮助文本，不改配置、退出码和命令行为。`python3 -m unittest discover -s tests` 43 项通过。已执行不带 `--config` 的 `./install.sh`。随 v0.3.0 发布到 `main`。

## 1. 背景

`multi-grok --help` 把 24 个子命令平铺成一列（`src/multi_grok/cli.py` `_parser`，基线约 87–208 行），没有分组，也没有示例。`multi-grok add --help` 只有参数说明。

multi-claude 的做法在 `src/multi_claude/cli.py`：顶层用 `epilog` + `RawDescriptionHelpFormatter` 按用途分组，并附 `Examples:`；子命令不传 `help=`，用途放在 `description=`，因此不会在顶层再平铺一次。补全从 `_SubParsersAction.choices` 取名字，不依赖 `help`。

## 2. 目标 / 非目标

**目标**

1. 顶层 `--help` 按用途分组列出全部子命令，并附 4 条起步示例。
2. 每个子命令的 `--help` 保留原来的用途说明，并在末尾列出该命令已经成立的用法。

**非目标**

- 不改任何命令的参数、退出码、写入行为和人读输出（`--help` 除外）。
- 不重排 README。
- 不把 `list` 改成简表，也不加 PATH 安装提示。那些是 multi-claude 帮助方案里的其它项。

## 3. 假设与约束

- 子命令分组沿用 multi-claude：`add_parser` 不传 `help=`，改传 `description=`。新增子命令必须同时写进 `COMMAND_GROUPS`、`COMMAND_HELP`、`COMMAND_EXAMPLES`，测试核对三者与解析器全集一致。
- 示例只复述 README 与当前命令实现里已经成立的用法。不新增参数，不把占位符写成真实会话号或凭据。
- 无参数时仍是 `list`。子解析器不设 `required`。
- 帮助文案保持英文，与现有 CLI 字符串一致。

## 4. 涉及模块

| 区域 | 行号锚点（改动前） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| `src/multi_grok/cli.py` | `_parser` 87–208 | 修改 | 分组 epilog、子命令 description 与 Examples |
| `tests/test_help.py` | 新文件 | 新增 | 顶层分组与每个子命令的示例 |
| `CHANGELOG.md` | `## 0.3.0` | 修改 | 一条帮助说明 |
| `docs/feature/feature-account-manager.md` | 文首注记 | 修改 | 指向本文 |
| `README.md`、`README.zh-CN.md` | Development / 开发 末段 | 修改 | 方案链接补上本文 |

## 5. 方案

### 5.1 实现要点

顶层 `epilog` 四个分组，命令名集合等于解析器子命令全集：

- Getting started：`add`、`login`、`list`
- Account settings：`set`、`proxy`、`env`、`args`、`rename`、`remove`、`export`、`import`
- Everyday：`usage`、`sessions`、`route`、`which`、`handoff`、`mcp`、`run`、`path`、`code`
- Setup and checks：`init`、`apply`、`doctor`、`completion`

顶层示例：

```text
multi-grok add home
multi-grok login home
multi-grok route ~/src/work work
multi-grok add --help          details of one command
```

每个子命令末尾：

```text
Examples:
  multi-grok <command> ...
```

`Examples:` 之后的每一行都是该子命令的一条用法，以 `multi-grok <该命令>` 开头。

### 5.2 接口变更

| 接口 | 变更 | 兼容性 |
| ---- | ---- | ---- |
| `multi-grok --help` | 分组列表加起步示例，不再平铺子命令 | 人读 |
| `multi-grok <command> --help` | 保留原用途说明，末尾增加 Examples | 人读 |

无配置、无环境变量、无退出码变更。

不涉及 `docs/reference/*`。

## 6. 备选方案与决策

- **保留平铺列表，只在末尾追加 Examples**：同一批命令会出现两次，和 multi-claude 已否定的做法相同，因此不采用。
- **只在顶层 Examples 里写完全部用法**：顶层会过长，子命令 `--help` 仍然没有示例，因此不采用。

## 7. 影响分析

**正向**

- 只改 `format_help()` 的文本。`run()` 在 `parse_args` 打印帮助后就返回，不进 `_dispatch`，不写 `config.json`。
- 补全读的是选项字符串和 `choices`，不读 `help`。`completion.collect`（`src/multi_grok/completion.py`）行为不变。

**反向**

- 无参数入口仍走 `command is None` → `cmd_list`（`cli.py` `_dispatch`）。子解析器不设 `required`，这条路径保持。
- 写命令的 `--dry-run` / `--verbose` 仍由 `parents=[common]` 挂上，只是帮助里的位置跟着 argparse 的选项区，不改变解析。

运行时没有新进程、新锁或新网络请求。`--help` 不读 `auth.json`。

## 8. 回归测试

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| T1 | `multi-grok --help` | 含四个分组标题和 `Examples:`；分组命令名无重复且等于解析器全集；不出现四空格平铺的 `init` 行；四条起步示例都在 |
| T2 | 每个子命令 `--help` | 含原来的用途说明；`Examples:` 后每条非空行都以 `multi-grok <该命令>` 开头，且与 `COMMAND_EXAMPLES` 一致 |
| T3 | 现有用例 | `python3 -m unittest discover -s tests` 全部通过 |

## 9. 日志 / 观测点

帮助文本不写日志。观察点就是 `--help` 的标准输出：顶层有 `Examples:`，子命令末尾有以 `multi-grok <命令>` 开头的示例行。
