# 补全参数取值、会话别名与 0.4.0 发布配套

> 2026-11-05 落地注记：代码已落地，未提交。用例放在新文件 `tests/test_completion_values.py`（不是下文写的 `tests/test_more.py:171` 附近），84 项全部通过；`/bin/bash` 3.2 实跑补全正确，本机没有 fish，`fish -n` 未跑。源码位置（落地后）：`src/multi_grok/completion.py:78` `value_spec`；`src/multi_grok/cli.py:84` `_resolve_command_alias`、`:1639` `cmd_completion`。
>
> 2026-11-05 方案注记：基线 `main` `d55bcea`（v0.3.0），下文 `file:line` 都指这个基线。参照 multi-codex 0.13.0 的 `conversations` 别名（multi-codex `src/multi_codex/cli.py:511-525`）和 0.15.0 的补全取值（multi-codex `docs/feature/feature-completion-values.md`）。网络检查本身见 `docs/feature/feature-network-check.md`。只做 bash / zsh / fish，PowerShell 随 0.5.0 的 Windows 支持一起做。

## 1. 背景

`multi-grok completion bash|zsh|fish` 输出补全脚本（`src/multi_grok/completion.py`）。现状能补：

- 子命令名和各子命令的选项名：生成时从 argparse 读出（`completion.py:47-64`）；
- 账号名：`POSITIONAL_KINDS` 登记的位置参数（`completion.py:17-24`）和 `--default` / `--from` 之后（`completion.py:28`），按 Tab 时调用 `multi-grok list --names`；
- 手写的固定取值：`proxy NAME` 之后的 `off inherit`、`completion` 之后的 shell 名（`completion.py:29`）。

补不到的位置：

| 位置 | 应补出 | 取值来源 |
| ---- | ---- | ---- |
| `usage --by` 之后 | `day model` | argparse `choices`（`cli.py:391`） |
| `netcheck NAME` 之后 | `off basic strict` | argparse `choices`（网络检查方案新增） |
| `netcheck` 的 `--clash` / `--clash-tun` / `--clash-global` 之后 | `on off` | argparse `choices`（同上） |
| `clash-api` 之后 | `auto off` | 关键字，不在 argparse 里 |
| `add` / `set` 的 `--shared-exclude` / `--shared-include` 之后 | 已配置的共享项名 | `config.shared_items`（`config.py:101`） |

现状里带值选项之后：bash 输出空候选（`completion.py:119`），zsh 不补（`:190`），fish 只声明该选项要跟一个值（`-r`，`:265`；脚本开头有全局 `complete -c multi-grok -f`，`:223`，fish 此时实际给出什么本机未装 fish、未核实）。

multi-codex 0.13.0 还给 `sessions` 加了别名 `conversations`。multi-grok 已有 `sessions`（`cli.py:395-400`），只缺这个别名。

## 2. 目标 / 非目标

**目标**

1. 上表 5 处在 bash / zsh / fish 三种补全脚本里都能补出候选；以后新增带 `choices` 的参数不用再改 `completion.py`。
2. `multi-grok conversations ...` 与 `multi-grok sessions ...` 等价。
3. 0.4.0 发布配套：版本号、CHANGELOG、README 中英两份写入新命令与退出码 5，README 改用绝对链接，CI 检查 README 里没有相对链接。

**非目标**

- `--opt=value` 写法的值补全（现状也不支持）。
- `--country`、`--proxy`、`--grep` 等自由取值。
- `grok-<名>` 启动命令的补全。
- PowerShell 补全（0.5.0）。
- 不在补全里列出 `conversations`（multi-codex 也没有列；正式名是 `sessions`）。
- README 演示动画与 `CONTRIBUTING.md`（见 §10）。

## 3. 假设与约束

- bash 脚本要能在 macOS 自带 bash 3.2 上运行：不用关联数组，取值表写成 `case`。
- argparse 的 `choices` 沿用 `collect` 对 `parser._actions` 的遍历读取（`completion.py:50-53`）。`CommandSpec` 与 `collect` 的签名不变，调用点只有 `completion.py:68`（`grep -rn "collect(" src tests`）。
- 隐藏选项（`help=argparse.SUPPRESS`）不进补全。基线没有隐藏选项（`grep -n SUPPRESS src/multi_grok/cli.py` 为 0），这条规则只影响本轮新增的 `--list-shared-items`。
- **与网络检查方案的接口约定**：`netcheck` 的档位位置参数用 `choices=("off", "basic", "strict")`，`--clash`、`--clash-tun`、`--clash-global` 用 `choices=("on", "off")`（网络检查方案 §5.1.5 已同步写明），否则 `value_spec` 读不到取值。
- **实施顺序**：网络检查方案先落地（同一次提交里先做），本方案的 C1、C2（`clash-api`）、C4 依赖它新增的子命令。

## 4. 涉及模块

| 区域 | 行号锚点（基线） | 改动类型 | 改动点 |
| ---- | ---- | ---- | ---- |
| 补全表 | `completion.py:17-29` | 修改 | `POSITIONAL_KINDS` 加 `"netcheck": ["name"]`、`"clash-api": ["clash"]`；`FIXED_WORDS` 加 `"clash": ("auto", "off")`；新增 `SHARED_OPTIONS = ("--shared-exclude", "--shared-include")` |
| 读取取值 | `completion.py:64` 之后 | 新增 | `value_spec(parser)` 返回 `(positional, options)`：`{(子命令, 序号): [词]}`、`{(子命令, 选项): [词]}`，只收有 `choices` 的参数 |
| 选项名 | `completion.py:57-64` | 修改 | `_options`、`_value_options` 跳过 `help == SUPPRESS` 的选项 |
| 入口 | `completion.py:67-73` `render` | 修改 | 另调 `value_spec`，把两张表传给三个生成函数 |
| bash | `completion.py:80-153` | 修改 | 跳过选项值的分支（`:115-120`）里，把 `*)` 分支换成查共享项和取值表的 case（路径类、账号类两条分支不动）；位置参数在 `kind` 为空时查取值表（`:128-131` 之后）；`kind` 分支（`:132-139`）加 `clash`、`shared` |
| zsh | `completion.py:156-216` | 修改 | 同 bash（`:186-191`、位置参数 case、`kind` 分支） |
| fish | `completion.py:219-283` | 修改 | 带取值的选项生成 `-x -a '词'`，共享项选项生成 `-x -a '(multi-grok completion --list-shared-items 2>/dev/null)'`（`:262-268`）；位置参数按取值表补一行（`:271-281` 之后）；`clash` 走 `FIXED_WORDS` |
| 命令行 | `cli.py:421-422` | 修改 | `shell` 改为 `nargs="?"`；新增隐藏选项 `--list-shared-items` |
| 命令行 | `cli.py:478-479`、`:1319-1322` | 修改 | `cmd_completion(args)`：`--list-shared-items` 输出 `config.shared_items` 每行一个，配置读不了时静默输出空、退出 0；既没给 shell 也没给该选项时退出 2 |
| 别名 | `cli.py:45` 与 `:46` 之间 | 新增 | `_COMMAND_ALIASES = {"conversations": "sessions"}` 与 `_resolve_command_alias`，照搬 multi-codex `cli.py:511-525` |
| 帮助 | `cli.py:103`、`:145` | 修改 | `sessions` 的说明加 “(also `conversations`)”；示例表不加别名示例（`tests/test_help.py:65-66` 要求示例以 `multi-grok sessions` 开头，与 multi-codex 一致） |
| 版本 | `src/multi_grok/__init__.py` | 修改 | `0.4.0` |
| CHANGELOG | `CHANGELOG.md:3` 之前 | 新增 | `## 0.4.0`：网络检查、Clash 检测、`clash-api`、退出码 5、补全取值、`conversations`、新旧版本不能混用 |
| README | `README.md:3`、`:154`、`:216`、`:220`；中文版 | 修改 | 英文版相对链接改成 `https://github.com/jakoes-wu/multi-grok/blob/main/...`（`:39` 是页内锚点，不改）；中文版按规范保留相对链接；头部加徽章；中英两份同步加网络检查一节、命令表新行、退出码 5、补全取值 |
| CI | `.github/workflows/ci.yml:9-19` | 修改 | 矩阵加一项 `ubuntu-22.04` + Python 3.8（与 `pyproject.toml:11` 的 `>=3.8` 对齐）；末尾加 “README links” 一步，照搬 multi-codex `ci.yml:41-45`，仓库名改为 multi-grok |
| 测试 | 新文件 `tests/test_completion_values.py` | 新增 | §8 的补全与别名用例 |

## 5. 方案

### 5.1 实现要点

#### 5.1.1 读取取值

`value_spec(parser)` 遍历每个子解析器的 `_actions`：

- 没有 `option_strings` 的是位置参数，按出现顺序编号（从 0 开始），有 `choices` 的记入 `positional[(子命令, 序号)]`；
- 有 `option_strings` 且有 `choices` 的，每个选项写法记入 `options[(子命令, 选项)]`；
- `choices` 一律转成字符串列表，保持声明顺序。

编号规则与脚本里数位置参数的规则一致：跳过选项及其值，遇到 `--` 停止（`completion.py:102-113`）。脚本逐词计数，所以：

- 遇到 `nargs` 为 `"*"`、`"+"` 或整数的位置参数，从它开始不再编号（它会吃掉多个词，后面的序号对不上），照 multi-codex `completion.py:81-90`；
- 带 `choices` 的位置参数 `nargs` 不是 `None` 或 `"?"` 时，生成阶段 `raise ValueError`，逼新增参数的人处理；
- 已知限制：`nargs="?"` 的参数被省略时，后面参数的序号会错位。基线和本轮都没有这种 “可省略参数后面还跟带取值参数” 的情况（`completion` 的 `shell` 改为可选后是唯一的位置参数）。

`POSITIONAL_KINDS` 里已登记的位置优先：`completion:0` 仍是 `shell`，`proxy:1` 仍是 `off inherit`，不受取值表影响。

#### 5.1.2 bash / zsh

生成两个 case 片段：

```sh
# 位置参数：在 kind 为空时查
case "$cmd:$npos" in
  netcheck:1) candidates="off basic strict" ;;
esac
# 选项值：替换现状 `*) COMPREPLY=(); return 0 ;;`
case "$cmd:$prev" in
  add:--shared-exclude|add:--shared-include|set:--shared-exclude|set:--shared-include) kind=shared ;;
  usage:--by) candidates="day model" ;;
  netcheck:--clash|netcheck:--clash-tun|netcheck:--clash-global) candidates="on off" ;;
  *) COMPREPLY=(); return 0 ;;
esac
```

`kind` 分支新增：

- `clash) candidates="auto off" ;;`
- `shared) candidates="$(multi-grok completion --list-shared-items 2>/dev/null)" ;;`

zsh 同样写法，`*)` 分支保持 `return`。选项值没在表里时行为与现状一致。

#### 5.1.3 fish

- 选项：在 `completion.py:262-268` 的循环里，选项在取值表中时用 `-x -a '<词>'` 代替 ` -r`，是共享项选项时用 `-x -a '(multi-grok completion --list-shared-items 2>/dev/null)'` 代替 ` -r`；其它选项不变。
- 位置参数：对取值表中 `POSITIONAL_KINDS` 没登记的 `(子命令, 序号)`，按现有 `__multi_grok_positionals` 条件补一行 `-a '<词>'`。

#### 5.1.4 `completion --list-shared-items`

- 用 `load_config` 读配置，输出 `shared_items` 每行一个，退出 0；`ConfigError` / `OSError` 时什么都不输出，退出 0（补全过程中不能把错误打到终端上）。
- 选项 `help=argparse.SUPPRESS`，不出现在 `completion --help` 与补全里。
- `completion` 既没给 shell 也没给这个选项时，报 `completion needs bash, zsh or fish` 退出 2；两者同时给出时报 `--list-shared-items takes no shell` 退出 2。

#### 5.1.5 会话别名

`run()` 在 `cli.py:45` 取得原始参数之后、`:46` `_split_forwarded` 之前，调用 `_resolve_command_alias(raw)`：第一个不以 `-` 开头的词是 `conversations` 时换成 `sessions`，其它位置的同名词（比如名叫 `conversations` 的账号）不动。网络检查方案的 `netgate` 拦截放在别名替换之前。

别名不进 argparse，所以分组帮助、`COMMAND_HELP`、`COMMAND_EXAMPLES`、补全、`args.command` 都不用处理两个名字，`test_help.py:29-47` 的覆盖检查不受影响。

#### 5.1.6 README 与 CI

- 改 README 前完整读取 `~/.claude-shared/skills/github-readme/SKILL.md`，按其事实核对与双语同步要求写。本轮做到：英文版相对链接全部改为绝对链接；头部按规范顺序加徽章 Release（`v0.3.0` 已在 GitHub 发布）/ CI / Python 3.8+ / License MIT / Platform `macOS | Linux`，不加 PyPI（2026-11-05 查 `https://pypi.org/pypi/multi-grok/json` 返回 404，没有发布）；Python 3.8+ 的依据是 CI 矩阵新加的 3.8；新增 “Network check” 一节（档位、Clash 两项、`clash-api auto`、跳过与关闭写法、Grok 实际是否走代理未验证）；命令表加 `netcheck`、`clash-api`；退出码句加 5；补全一节写明会补出取值。
- CI 矩阵改为显式列出系统：`runs-on: ${{ matrix.os }}`，`matrix.include` 列 `{os: ubuntu-latest, python: "3.11"}`、`{os: ubuntu-latest, python: "3.12"}`、`{os: ubuntu-22.04, python: "3.8"}`（3.8 放在 22.04 上，新版 Ubuntu 上 setup-python 是否还提供 3.8 未核实）。
- CI 加：

```yaml
      - name: README links
        run: |
          if grep -noE '\]\([^)]+\)' README.md | grep -vE '\]\((https?://|#)'; then
            echo 'README.md has relative links; use https://github.com/jakoes-wu/multi-grok/blob/main/... instead'; exit 1
          fi
```

### 5.2 接口变更

| 类型 | 名称 | 变化 | 兼容性 |
| ---- | ---- | ---- | ---- |
| 子命令别名 | `conversations` | 新增，等同 `sessions` | 新增 |
| 选项 | `completion --list-shared-items` | 新增，隐藏 | 新增 |
| 位置参数 | `completion SHELL` | 改为可选；不给且没给 `--list-shared-items` 时退出 2 | 原有调用不变；原来缺参也退出 2 |
| 补全脚本 | bash / zsh / fish | 多补出取值；用户需重新生成脚本才生效 | 已装的旧脚本照常可用 |

无 `docs/reference/*` 改动，不涉及 reference sibling 回补。

## 6. 备选方案与决策

1. **argparse `aliases=["conversations"]`**：否决。别名会进入 `action.choices`，分组帮助、示例表、补全、`args.command` 都要处理两个名字，`test_help.py:36-40` 也要改。multi-codex 用的是解析前替换，照搬。
2. **取值全部手写在 `completion.py`**：否决。参数改了容易漏改，从 `choices` 读出来就不会。

## 7. 影响分析

- `completion.render` 多读一张表，只在生成脚本时运行，不影响其它命令。
- 补全脚本按 Tab 时，只有在共享项选项之后才多调一次 `multi-grok completion --list-shared-items`（一次 Python 启动，读 `config.json`）。
- `cmd_completion` 签名从 `shell` 改为 `args`：调用点只有 `cli.py:479`。
- `_resolve_command_alias` 在所有命令之前运行，只替换子命令位置上的 `conversations`。账号名 `conversations` 作为参数（如 `multi-grok sessions conversations`）不受影响；把 `conversations` 当子命令位置之外的词也不受影响。
- `_options` 跳过隐藏选项：基线没有隐藏选项，现有补全输出不变。
- README 只改文字与链接；CI 新的 README 链接检查只读 README。
- CI 新增 3.8 一项是新的通过门槛：现有代码与测试若在 3.8 上失败，先单独修复再合入（本机只有 3.9.6，无法实跑 3.8）。

## 8. 回归测试

全部在临时 `HOME` 下跑（`tests/helpers.py` 的 `isolated_home`）。

| 编号 | 用例 | 判据 |
| ---- | ---- | ---- |
| C0 | `value_spec`：构造一个 `nargs="*"` 位置参数后跟带 `choices` 参数的解析器，后者不登记；构造一个 `nargs="*"` 且带 `choices` 的参数，抛 `ValueError` | 单元断言 |
| C1 | bash 脚本：`usage:--by` 有 `day model`，`netcheck:1` 有 `off basic strict`，`netcheck:--clash-tun` 有 `on off`，有 `kind=shared`，`clash) candidates="auto off"` | 字符串包含 |
| C2 | 用 bash 实跑补全函数（没有 bash 时跳过；PATH 最前面放一个假 `multi-grok`：`PYTHONPATH=<仓库>/src exec python3 -m multi_grok "$@"`，不用本机已装的 0.3.0；发版前另在 macOS 用 `/bin/bash` 3.2 本地跑一遍）：设 `COMP_LINE`、`COMP_POINT` 后调 `_multi_grok`，`multi-grok usage --by ` 得到 `day model`；`multi-grok clash-api ` 得到 `auto off`；`multi-grok proxy home ` 仍得到 `off inherit`；`multi-grok add x --shared-exclude ` 得到配置里的共享项 | `COMPREPLY` |
| C3 | zsh 脚本 `zsh -n` 通过；fish 脚本 `fish -n` 通过（没装对应 shell 时跳过） | 退出码 0 |
| C4 | fish 脚本里 `-l clash` 那一行含 `-x`、含 `-a 'on off'`、不含 `-r`；共享项选项那一行含 `--list-shared-items` | 分片段包含 |
| C5 | `completion --list-shared-items` 输出 `skills commands agents`（默认），配置损坏时输出空、退出 0 | 输出与退出码 |
| C6 | `completion` 无参数退出 2；`completion bash --list-shared-items` 退出 2；`completion --help` 与三种补全脚本都不含 `--list-shared-items` | 退出码 / 不包含 |
| C7 | `conversations` 与 `sessions` 输出相同；`sessions conversations` 把 `conversations` 当账号名 | 输出 |
| C8 | README 相对链接检查命令在本地运行无输出；推送后 CI 的 3.8 一项通过 | `grep` 为空 / CI 结果 |
| C9 | 原有补全用例（`test_more.py:171`、`test_reports.py:278`）与全部测试通过 | `python3 -m unittest discover -s tests` |

## 9. 日志 / 观测点

- 补全没有日志；判断依据是 C2 实跑得到的 `COMPREPLY`。
- `--list-shared-items` 出错时静默是有意的，排查用 `multi-grok init` 或 `multi-grok doctor` 看配置错误。

## 10. 开放问题

- README 规范要求的演示动画（`docs/assets/demo.gif`）和 `CONTRIBUTING.md` 本轮不做：它们与复刻的三个版本无关，做演示需要另录一段虚构数据的终端动画。需要时另开一项。
