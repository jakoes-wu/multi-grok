# multi-grok

[English](README.md) | **简体中文**

在一台机器上同时使用多个 Grok 账号。每个账号有自己的登录、配置和会话，用 `grok-<账号名>` 启动。例如账号 `home` 的命令是 `grok-home`。

本工具不读取、不复制、不打印 `auth.json` 的内容，也不修改 shell 启动文件。直接运行 `grok` 仍然使用原来的 `~/.grok`，不受影响。

```sh
grok-home          # 账号 home，目录 ~/.grok-accounts/home
grok-work          # 另一个终端里的另一个账号
multi-grok         # 查看账号、启动命令和登录文件是否存在
```

## 它做了什么

Grok 用环境变量 `GROK_HOME` 决定数据目录，不设置时用 `~/.grok`。multi-grok 给每个账号建一个真实目录，并写一个启动命令：

```text
grok-home    ->  GROK_HOME=$HOME/.grok-accounts/home
grok-work    ->  GROK_HOME=$HOME/.grok-accounts/work
grok         ->  不变，仍然是 ~/.grok
```

启动命令是普通 shell 脚本。卸掉 multi-grok 之后，这些命令还能用。

Grok 在沙箱启动时拒绝符号链接形式的 `GROK_HOME`。账号目录必须是真实目录，启动命令发现软链会直接退出。

## 功能

- 每个账号一个目录：默认 `~/.grok-accounts/<账号名>`，权限 `0700`。
- 启动命令 `grok-<账号名>`，写在 `~/.local/bin`。它调用 `PATH` 里的 `grok`，不把程序复制进账号目录。
- `multi-grok login <账号名>` 等价于 `grok-<账号名> login`。登录写进该账号自己的 `auth.json`。
- 可选的每账号代理：本机端口、`http`/`https` URL、`off` 或 `inherit`。
- 额外环境变量和固定参数：全局默认值加上账号自己的值。你在 `grok-<账号名>` 后面再输入的参数排在最后。
- 新建账号时写入一份受管的 `config.toml`，关掉对 `~/.claude` 和 `~/.cursor` 的兼容扫描。这两处路径不随 `GROK_HOME` 改变；不关掉的话，各账号仍会读到同一套 Claude / Cursor 技能和规则。已有的 `config.toml` 不会被覆盖。
- 启动命令会清除 `XAI_API_KEY`、`GROK_CODE_XAI_API_KEY`、`GROK_AUTH`、`GROK_AUTH_PATH`、`GROK_CONFIG`、`GROK_CONFIG_PATH`。父 shell 里导出的这些变量不再串到别的账号上。直接运行 `grok` 不受影响。
- 写操作可重复执行。和本工具无关的同名文件算冲突，这时什么都不写。`remove` 只注销账号并删除启动命令，不删除账号目录。
- 可选的共享技能、按目录选择账号（`grok-auto`）、会话列表，以及把一个会话目录复制到另一个账号。见[日常命令](#日常命令)。

## 环境

| | 需要 |
| ---- | ---- |
| 系统 | macOS 或 Linux（WSL 按 Linux） |
| 工具 | Python 3.8+（`python3`，只用标准库）、已经装好的 `grok` |

Windows 本身暂不支持。

## 安装

在克隆下来的仓库里：

```sh
git clone https://github.com/jakoes-wu/multi-grok.git
cd multi-grok
./install.sh
```

程序装到 `~/.local/share/multi-grok`，命令 `multi-grok` 装到 `~/.local/bin`。换位置用 `--prefix DIR`。安装脚本不改 `~/.zshrc` 或 `~/.bashrc`。如果 `~/.local/bin` 不在 `PATH` 里，它只打印一行要你自己加的 `export`。

发布包还没有。请从克隆安装，不要用 `curl | sh`。

`./install.sh --help` 可以看到全部选项。`--uninstall` 只删工具本身，配置、账号目录和 `grok-<账号名>` 都保留。

## 开始用

```sh
multi-grok add home
multi-grok login home          # 或：grok-home login
grok-home                      # 以后都用这个命令
```

再加一个账号：

```sh
multi-grok add work --proxy 7890
multi-grok login work
grok-work
```

看状态：

```sh
multi-grok
multi-grok doctor
```

`login=file` 只表示 `auth.json` 这个普通文件存在，不表示令牌有效。`login=missing` 表示还没登录。

## 代理

| 值 | 启动命令里的效果 |
| ---- | ---- |
| `inherit`（默认） | 不动代理变量，沿用当前 shell |
| `off` | 清除 `HTTPS_PROXY`、`HTTP_PROXY`、`ALL_PROXY`、`NO_PROXY` 以及对应的小写变量 |
| `7890` | 等于 `http://127.0.0.1:7890` |
| `http://主机:端口`、`https://主机:端口` | 设置上面四个 http(s) 变量，清除继承来的 `ALL_PROXY`，并在原有 `NO_PROXY` 后面加上 `localhost,127.0.0.1,::1` |

带用户名或密码的 URL 会拒绝，因为启动命令是所有人可读的文件。本工具只把 `http` 和 `https` 写进启动命令。

Grok 用户指南在钩子一节写到请求能看见 `HTTPS_PROXY`。各子系统是否都走这个变量，以 Grok 自己的行为为准。

## 环境变量和固定参数

```sh
multi-grok env --defaults LANG=C
multi-grok env work GROK_SANDBOX=off --unset LANG
multi-grok args work -- --permission-mode acceptEdits
```

- 账号自己的环境变量覆盖同名的全局默认值。固定参数是全局的在前、账号的在后。
- 值恰好是 `~` 或以 `~/` 开头时展开成绝对路径。其它写法原样写入，不做 `$` 展开。
- `GROK_HOME`、`XAI_API_KEY`、代理变量，以及名字像令牌、密码的变量，都不能写进启动命令。
- `args` 必须带上 `--`。`--` 后面为空表示清空。最后一个固定参数如果还要接一个值（例如单独一个 `--resume`），它会把你随后输入的提示词吃掉；这时会有告警。

## 和默认的 ~/.grok 的关系

`~/.grok` 继续只属于直接运行的 `grok`。multi-grok 不迁移它，也不把它改成软链。账号目录不能放在 `~/.grok` 里面，也不能把 `~/.grok` 包进账号目录。

已经在 `~/.grok` 里登录过的账号，不会自动出现在 `grok-home` 里。`home` 只是一个账号名。需要的话在 `grok-home` 里重新登录。

## 命令

| 命令 | 作用 |
| ---- | ---- |
| `init [--root DIR] [--bin-dir DIR] [--shared-dir DIR]` | 设置账号根目录、启动命令目录，以及可选的共享目录。已有账号时不能改根目录 |
| `add <名称> [--proxy 值] [--no-isolate] [--shared [DIR]]` | 登记账号并写 `grok-<名称>`。重复执行是安全的。不加 `--shared` 就不做共享链接 |
| `set <名称> [--proxy 值] [--shared [DIR] \| --no-shared]` | 改已有账号的代理、隔离或共享 |
| `remove <名称>` | 注销、删除启动命令，并删除本工具建的共享链接。目录、`auth.json`、会话都保留 |
| `rename <旧名> <新名>` | 改启动命令。目录和登录文件留在原处 |
| `login <名称> [-- grok 的参数]` | 在该账号的 `GROK_HOME` 里执行 `grok login` |
| `proxy <名称> <端口 \| URL \| off \| inherit>` | 设置代理 |
| `env (<名称> \| --defaults) [键=值 ...] [--unset 键]` | 设置额外环境变量 |
| `args (<名称> \| --defaults) -- [参数 ...]` | 整体替换固定参数 |
| `apply [-f 文件]` | 按当前配置或一份 JSON 把目录和启动命令收敛到一致 |
| `list [--json \| --names]` | 只读列出状态，含从 `summary.json` 取出的 `last=`。不带子命令时也是它 |
| `doctor [--json]` | 检查 `PATH`、目录、启动命令、路由脚本和 `auth.json` 的类型。PATH 里更靠前的 `grok-<名称>` 只是警告 |
| `path <名称>` | 打印账号目录 |
| `run [<名称>] -- <命令...>` | 用该账号的环境运行一条命令。必须带 `--` |
| `route <目录> <名称>` | 让 `grok-auto` 在这个目录及其子目录里使用该账号 |
| `which [目录]` | 打印 `grok-auto` 会选中的账号 |
| `mcp <名称> [grok mcp 的参数...]` | 在该账号里执行 `grok mcp` |
| `usage <名称> <会话 id> [轮次]` | 在该账号里执行 `grok usage` |
| `usage [<名称>] --history [--days N] [--by day\|model] [--json]` | 把 `grok usage` 里的 token 和费用数字加总。不写名称时包含每个已登记账号和 `~/.grok`。默认最近 7 天 |
| `sessions [<名称>] [--grep 文本] [--cmd] [--json]` | 只从 `summary.json` 列会话。不写名称时，`~/.grok` 会以 `default` 出现 |
| `handoff <目标> [--from 名称] --session ID [--force]` | 复制一个会话目录。不改写 `summary.json` |
| `export <名称> <文件>` / `import <文件> <名称> [--force]` | 打包或还原技能等共享目录。不含登录，不含会话 |
| `completion bash\|zsh\|fish` | 打印补全脚本。不改 shell 启动文件 |
| `code <名称> [路径] -- [参数...]` | 实验性。单独的 VS Code 用户数据目录，加上该账号的环境 |

写命令都接受 `--dry-run` 和 `--verbose`。退出码：`0` 成功，`1` 错误，`2` 用法错误，`3` 有冲突所以什么都没写，`4` 另一个 multi-grok 正在写配置。

示例配置见 [examples/config.example.json](examples/config.example.json)。

```sh
multi-grok apply -f examples/config.example.json
```

`apply -f` 以文件为准。文件里没有的账号会被注销（启动命令删除，目录保留）。

## 日常命令

几个账号共用一份技能目录。账号目录仍是真实目录，里面的 `skills` 变成软链：

```sh
mkdir -p ~/.grok-shared/skills
multi-grok set home --shared ~/.grok-shared
```

可以共享的名字：`skills`、`commands`、`agents`、`plugins`、`workflows`、`hooks`、`rules`、`personas`。默认是前三个。会话、记忆、日志、`auth.json`、`config.toml` 不会被链出去。目标位置已经是真实目录时算冲突，这时什么都不写。`--adopt` 把你自己早就指对的软链记下来，之后 `--no-shared` 才会删它。

按当前目录选账号：

```sh
multi-grok route ~/src/work work
multi-grok route --default home
grok-auto
multi-grok which
```

最长的、并且进得去的目录赢。没有命中、也没有默认账号时，`grok-auto` 运行 `PATH` 里的 `grok`，仍使用 `~/.grok`。

跨账号列会话，不打开对话日志：

```sh
multi-grok sessions --grep launcher --cmd
multi-grok usage home 会话ID
multi-grok usage home --history --days 7
multi-grok handoff work --from home --session 会话ID
```

`--cmd` 会带上 `cd` 和 `grok-<名称> --resume`，因为 Grok 按当前目录匹配恢复用的标题。`~/.grok` 里的会话标成 `default`，这一行的 `--cmd` 用的是不带名字的 `grok`。交接只复制会话目录，不改 `summary.json`。复制过去之后能否 `--resume`，以 Grok 自己的检查为准；用户指南没有写换一个 `GROK_HOME` 之后仍能恢复。

`usage --history` 对每个会话调用一次 `grok usage`，只加总数字，不打印对话。工作目录保持不变。用户指南没有写 `grok usage` 是否只看当前目录。

补全脚本只打印，不写入启动文件：

```sh
multi-grok completion zsh
```

## 不做的事

- 不移动 `~/.grok`，也不把它换成软链。沙箱拒绝软链形式的 `GROK_HOME`。
- 不查询 5 小时或 7 天额度。状态栏载荷里有本次会话的费用和上下文，用户指南写明没有额度摘要。`usage 名称 会话` 转给 `grok usage`。`usage 名称 --history` 只加总那份本地 JSON。
- 不读取 `auth.json`。
- 用户指南写了技能的 `SKILL.md` 可以是软链，没有写整个 `skills/` 目录是软链时怎么发现技能。共享条目仍使用这种目录软链，这样不必改写 `config.toml`。通过这层软链能不能被 Grok 扫到，尚未验证。

## 开发

```sh
python3 -m unittest discover -s tests -v
```

方案说明在 [docs/feature/feature-account-manager.md](docs/feature/feature-account-manager.md)、[docs/feature/feature-everyday-commands.md](docs/feature/feature-everyday-commands.md) 和 [docs/feature/feature-help-examples.md](docs/feature/feature-help-examples.md)。

## 许可

[MIT](LICENSE)
