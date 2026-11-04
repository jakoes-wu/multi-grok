# multi-grok

**English** | [简体中文](README.zh-CN.md)

Use several Grok accounts on one machine at the same time. Each account keeps its own login, settings and sessions. Start one with `grok-<name>`. The account named `home` is started with `grok-home`.

multi-grok never reads, copies or prints the contents of `auth.json`, and it never edits your shell startup file. A plain `grok` command still uses `~/.grok`.

```sh
grok-home          # account "home", directory ~/.grok-accounts/home
grok-work          # a different account, in another terminal
multi-grok         # accounts, launchers, and whether auth.json exists
```

## How it works

Grok stores its data in the directory named by `GROK_HOME` (default `~/.grok`). multi-grok creates one real directory per account and a launcher:

```text
grok-home    ->  GROK_HOME=$HOME/.grok-accounts/home
grok-work    ->  GROK_HOME=$HOME/.grok-accounts/work
grok         ->  unchanged; still ~/.grok
```

The launchers are plain shell scripts. They keep working if you uninstall multi-grok.

Grok refuses a symlinked `GROK_HOME` when the sandbox starts. Account directories are real directories. A launcher exits if its directory is a symlink.

## Features

- One directory per account, by default `~/.grok-accounts/<name>`, mode `0700`.
- A `grok-<name>` command in `~/.local/bin`. It runs whichever `grok` is on `PATH`. It does not copy the binary into the account directory.
- `multi-grok login <name>` is `grok-<name> login`. The login is written to that account's own `auth.json`.
- An optional per-account proxy: a local port, an `http` or `https` URL, `off`, or `inherit`.
- Extra environment variables and fixed arguments, with global defaults plus per-account values. Arguments you type after `grok-<name>` come last.
- A new account gets a managed `config.toml` that turns off the Claude and Cursor compatibility scans. Those scans read `~/.claude` and `~/.cursor`, which do not follow `GROK_HOME`. An existing `config.toml` is left untouched.
- Launchers unset `XAI_API_KEY`, `GROK_CODE_XAI_API_KEY`, `GROK_AUTH`, `GROK_AUTH_PATH`, `GROK_CONFIG` and `GROK_CONFIG_PATH`, so a key exported in the parent shell is not shared across accounts. Running `grok` directly is unchanged.
- Write commands are safe to re-run. A same-named file that multi-grok does not own is a conflict, and then nothing is written. `remove` unregisters the account and deletes the launcher. It does not delete the directory.
- Optional shared skills and commands, directory routing (`grok-auto`), session listing, and a copy of one session directory into another account. See [Everyday commands](#everyday-commands).

## Prerequisites

| | Needed |
| ---- | ---- |
| Operating system | macOS or Linux (WSL counts as Linux) |
| Tools | Python 3.8+ (`python3`, standard library only), and `grok` on `PATH` |

Windows itself is not supported.

## Installation

From a clone:

```sh
git clone https://github.com/jakoes-wu/multi-grok.git
cd multi-grok
./install.sh
```

The tool goes to `~/.local/share/multi-grok` and the `multi-grok` command to `~/.local/bin`. Use `--prefix DIR` to install somewhere else. The installer never edits `~/.zshrc` or `~/.bashrc`. If `~/.local/bin` is not on `PATH`, it prints the `export` line for you to add.

There is no release archive yet. Install from a clone.

`./install.sh --help` lists every option. `--uninstall` removes the tool only. Configuration, account directories and `grok-<name>` launchers stay.

## Quick start

```sh
multi-grok add home
multi-grok login home          # or: grok-home login
grok-home
```

A second account:

```sh
multi-grok add work --proxy 7890
multi-grok login work
grok-work
```

Check:

```sh
multi-grok
multi-grok doctor
```

`login=file` means `auth.json` is a regular file. It does not mean the token is valid. `login=missing` means this account has not logged in.

## Proxy values

| Value | Effect in the launcher |
| ---- | ---- |
| `inherit` (default) | Leaves proxy variables as they are in your shell. |
| `off` | Unsets `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY` and their lowercase forms. |
| `7890` | Same as `http://127.0.0.1:7890`. |
| `http://host:port`, `https://host:port` | Sets the four http(s) variables, unsets an inherited `ALL_PROXY`, and appends `localhost,127.0.0.1,::1` to `NO_PROXY`. |

URLs with a user name or password are rejected because launchers are world-readable. Only `http` and `https` URLs are written.

Grok's user guide mentions `HTTPS_PROXY` in the hooks section. Whether every subsystem uses that variable is Grok's own behavior.

## Environment variables and arguments

```sh
multi-grok env --defaults LANG=C
multi-grok env work GROK_SANDBOX=off --unset LANG
multi-grok args work -- --permission-mode acceptEdits
```

- An account value overrides a global default of the same name. Fixed arguments are the defaults, then the account's own, then whatever you type after `grok-<name>`.
- A value that is exactly `~` or starts with `~/` is expanded. Other values are written literally.
- `GROK_HOME`, `XAI_API_KEY`, the proxy variables, and names that look like tokens or passwords are rejected.
- `args` needs an explicit `--`. An empty list after `--` clears the arguments. If the last fixed argument still expects a value (for example a bare `--resume`), it would consume the next word you pass to the launcher. multi-grok warns about that.

## `~/.grok` stays where it is

`~/.grok` still belongs to a plain `grok` command. multi-grok does not migrate it and does not replace it with a symlink. An account directory must not sit inside `~/.grok`, and it must not contain `~/.grok`.

A login that already exists in `~/.grok` does not show up in `grok-home`. `home` is just an account name. Log in again inside that launcher.

## Commands

| Command | What it does |
| ---- | ---- |
| `init [--root DIR] [--bin-dir DIR] [--shared-dir DIR]` | Set the account root, the launcher directory, and the optional shared directory. The root cannot change while accounts exist. |
| `add <name> [--proxy VALUE] [--no-isolate] [--shared [DIR]]` | Register an account and write `grok-<name>`. Safe to re-run. Sharing is off unless you pass `--shared`. |
| `set <name> [--proxy VALUE] [--shared [DIR] \| --no-shared]` | Change proxy, isolate, or sharing on an account that already exists. |
| `remove <name>` | Unregister the account, delete its launcher, and delete shared links this tool created. The directory, `auth.json` and sessions stay. |
| `rename <old> <new>` | Rename the launcher. The directory and its login stay. |
| `login <name> [-- grok-args]` | Run `grok login` with that account's `GROK_HOME`. |
| `proxy <name> <port \| URL \| off \| inherit>` | Set the proxy. |
| `env (<name> \| --defaults) [KEY=value ...] [--unset KEY]` | Set extra environment variables. |
| `args (<name> \| --defaults) -- [args ...]` | Replace the fixed arguments. |
| `apply [-f FILE]` | Make directories and launchers match the current config, or a JSON file. |
| `list [--json \| --names]` | Read-only status, including `last=` from `summary.json`. This is also the default when you run `multi-grok` alone. |
| `doctor [--json]` | Check `PATH`, directories, launchers, the router, and the type of `auth.json`. A `grok-<name>` earlier on `PATH` is a warning. |
| `path <name>` | Print the account directory. |
| `run [<name>] -- <command...>` | Run a command with that account's environment. `--` is required. |
| `route <dir> <name>` | Send `grok-auto` to that account for this directory and its children. |
| `which [dir]` | Print the account `grok-auto` would pick. |
| `mcp <name> [grok mcp args...]` | Run `grok mcp` inside that account. |
| `usage <name> <session-id> [turn]` | Run `grok usage` inside that account. |
| `usage [<name>] --history [--days N] [--by day\|model] [--json]` | Sum token and cost numbers from `grok usage`. Omit the name to include every account and `~/.grok`. Default window is 7 days. |
| `sessions [<name>] [--grep TEXT] [--cmd] [--json]` | List sessions from `summary.json` only. With no name, `~/.grok` is included as `default`. |
| `handoff <target> [--from NAME] --session ID [--force]` | Copy one session directory. It does not rewrite `summary.json`. |
| `export <name> <file>` / `import <file> <name> [--force]` | Pack or unpack skills and the other shared directories. No login, no sessions. |
| `completion bash\|zsh\|fish` | Print a completion script. Nothing is written to your shell startup file. |
| `code <name> [path] -- [args...]` | Experimental. Separate VS Code user data, plus this account's environment. |

Write commands accept `--dry-run` and `--verbose`. Exit codes: `0` success, `1` error, `2` bad usage, `3` conflict (nothing written), `4` another multi-grok process holds the write lock.

See [examples/config.example.json](examples/config.example.json).

```sh
multi-grok apply -f examples/config.example.json
```

`apply -f` replaces the account list with the file. An account missing from the file is unregistered. Its directory stays.

## Everyday commands

Share one skills directory between accounts. The account directory stays real. The `skills` entry inside it becomes a symlink:

```sh
mkdir -p ~/.grok-shared/skills
multi-grok set home --shared ~/.grok-shared
```

Items you can share: `skills`, `commands`, `agents`, `plugins`, `workflows`, `hooks`, `rules`, `personas`. The default set is the first three. `sessions`, memory, logs, `auth.json` and `config.toml` are never linked. A directory that is already a real folder is a conflict, and then nothing is written. `--adopt` records a link you already made, so a later `--no-shared` will remove it.

Pick an account from the current directory:

```sh
multi-grok route ~/src/work work
multi-grok route --default home
grok-auto
multi-grok which
```

The longest existing directory wins. With no match and no default, `grok-auto` runs `grok` from `PATH`, which still uses `~/.grok`.

List sessions across accounts, without opening the conversation log:

```sh
multi-grok sessions --grep launcher --cmd
multi-grok usage home SESSION_ID
multi-grok usage home --history --days 7
multi-grok handoff work --from home --session SESSION_ID
```

`--cmd` prints `cd` and `grok-<name> --resume`, because Grok matches a resume title in the current directory. Sessions that live in `~/.grok` are labeled `default`, and that `--cmd` line uses plain `grok`. Handoff copies the session directory and leaves `summary.json` unchanged. Whether Grok then resumes that copy is Grok's own check; the user guide does not promise a resume after the files move to another `GROK_HOME`.

`usage --history` calls `grok usage` once per session and adds up the numbers. It does not print the conversation. The working directory is left as it is. The user guide does not say whether `grok usage` only sees the current directory.

Shell completion, printed only:

```sh
multi-grok completion zsh
```

## What this does not do

- It does not move `~/.grok` or replace it with a symlink. The sandbox refuses a symlinked `GROK_HOME`.
- It does not report a 5-hour or 7-day quota. Grok's status-line payload has session cost and context, and the user guide says a rate-limit summary is absent. `usage NAME SESSION` forwards to `grok usage`. `usage NAME --history` only sums that local JSON.
- It does not read `auth.json`.
- The user guide says a skill's `SKILL.md` may be a symlink. It does not say what happens when the whole `skills/` directory is a symlink. Shared items use that directory symlink anyway, so `config.toml` does not have to be rewritten. Treat skill discovery through that link as unverified.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Design notes: [docs/feature/feature-account-manager.md](docs/feature/feature-account-manager.md), [docs/feature/feature-everyday-commands.md](docs/feature/feature-everyday-commands.md), and [docs/feature/feature-help-examples.md](docs/feature/feature-help-examples.md).

## License

[MIT](LICENSE)
