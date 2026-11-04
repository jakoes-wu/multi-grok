# Security

multi-grok writes launchers as world-readable shell scripts. Do not put API keys, tokens or proxy passwords in `env` or `args`. The `env` command rejects names that look like credentials. The proxy command rejects URLs that contain a user name or a password.

The tool does not read `auth.json`. `list` and `doctor` only look at the file type (regular file, symlink, or missing).

Account directories created by this tool are mode `0700`. `doctor` reports a directory that is readable by the group or by others. It does not change the mode of a directory that already existed.

`--uninstall` does not delete account directories.

Report a vulnerability to the maintainer in private. Do not open a public issue that includes a token or the contents of `auth.json`.
