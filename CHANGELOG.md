# Changelog

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
