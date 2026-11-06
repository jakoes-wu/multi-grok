# Changelog

## Unreleased

- README: how to load completion in zsh, bash and fish.

## 0.4.0

Network check before an account starts, Clash checks, and completion values. macOS and Linux only; Windows comes later.

- `netcheck <name> [off|basic|strict] [--country CC]`: `basic` checks that the proxy port accepts a connection. `strict` also reads `https://cli-chat-proxy.grok.com/cdn-cgi/trace` directly and through the http proxy and compares `loc=` with the country (default `US`). With no setting, `netcheck <name>` runs the checks once.
- `--clash-tun`, `--clash-global`, `--clash on|off`: the local Clash (mihomo) controller must report TUN on and/or global mode. They are independent of the level.
- `clash-api [auto|off|ADDRESS] [--secret-stdin]`: show, find, clear or set the controller. `auto` reads running Clash / mihomo command lines and, on macOS, the Clash Verge config. Only local addresses are kept.
- When a check is on, `grok-<name>`, `grok-auto`, `run`, `login` and `code` refuse to start on failure with exit code `5`. `MULTI_GROK_SKIP_NETCHECK=1` skips one run. `mcp` and `usage` are not checked. Accounts without a check get the same launcher as before.
- `apply -f FILE` keeps the current `clash_api` and warns if the file has one.
- Completion offers fixed values (`usage --by`, `netcheck` levels, `on`/`off`, `clash-api auto|off`) and the configured shared item names.
- `conversations` is another name for `sessions`.
- README links are absolute; CI also runs on Python 3.8 and checks README links.

`config.json` gains `net_check` (per account) and `clash_api`. Version 0.3.0 rejects a file that has them as unknown keys, so do not mix 0.3.0 and 0.4.0 on one config.

## 0.3.0

Local reports. Nothing new is stored in `config.json`.

- `sessions` also lists `~/.grok` as `default`, unless an account is already named `default` or an account directory is already `~/.grok`. `--cmd` for that row uses `grok --resume`. A symlinked `~/.grok` is skipped.
- `list` shows `last=`, the newest `summary.json` `updated_at` for that registered account. `--names` stays names only.
- `usage [<name>] --history [--days N] [--by day|model] [--json]` runs `grok usage <id>` and sums the numbers. It does not print turn text. `--days` defaults to 7. `0` means no time cutoff. With no name, every registered account is included, and so is `~/.grok` under the label `default`.
- `doctor` warns when another `grok-<name>` is earlier on `PATH`. The warning does not change a successful exit code.
- `--help` groups the commands and ends with examples. Each command's own `--help` keeps its description and lists that command's examples.

`grok usage` is started with that account's `GROK_HOME` and the current directory unchanged. The user guide does not say whether the command is limited to the current directory.

## 0.2.0

Commands that map onto a documented Grok behavior:

- `set`, `rename`, `path`, `run`, `which`, `mcp`, `usage`, `sessions`, `handoff`, `export`, `import`, `completion`.
- `route` writes `grok-auto`. The longest existing directory wins. With no match and no default, it runs `grok` from `PATH`.
- Optional shared items. A new account does not link anything until you pass `--shared`. Links live inside the account directory. `GROK_HOME` stays a real directory.
- `list --json` / `--names`, `doctor --json`. Text list adds `shared=on|off`.
- `code` is experimental. It sets the account environment and a private VS Code user-data directory. The user guide does not say the editor extension reads `GROK_HOME`.

A managed `config.toml` that Grok has edited is left in place. v0.1 rewrote any file that still had the managed-by marker.

Not included, because Grok has no matching contract: migrating `~/.grok`, online plan quotas, a status-line rate-limit hook, and reading `auth.json`.

Config version stays 1. A v0.1 file still loads, and saving it does not add empty `shared` or `routes` objects.

## 0.1.0

First release.

- One real directory per account under `~/.grok-accounts`.
- A `grok-<name>` launcher that sets `GROK_HOME` and clears credential variables inherited from the shell.
- `init`, `add`, `remove`, `login`, `proxy`, `env`, `args`, `apply`, `list` and `doctor`.
- A managed `config.toml` that turns off Claude and Cursor compatibility scans, without overwriting a file you already have.
- `~/.grok` is not migrated and is not replaced with a symlink.
