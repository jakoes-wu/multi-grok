# Changelog

## 0.1.0

First release.

- One real directory per account under `~/.grok-accounts`.
- A `grok-<name>` launcher that sets `GROK_HOME` and clears credential variables inherited from the shell.
- `init`, `add`, `remove`, `login`, `proxy`, `env`, `args`, `apply`, `list` and `doctor`.
- A managed `config.toml` that turns off Claude and Cursor compatibility scans, without overwriting a file you already have.
- `~/.grok` is not migrated and is not replaced with a symlink.
