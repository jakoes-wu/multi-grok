# Contributing

Bugs and changes are welcome.

- Run `python3 -m unittest discover -s tests -v` before sending a change.
- Do not put tokens, passwords or a real `auth.json` in the repository, in a test, or in a launcher.
- Tests must use a temporary `HOME`. They must not read or write the developer's `~/.grok` or `~/.grok-accounts`.
- Comments in code are in Chinese. The command-line text and the English README stay in English. The Chinese README and the design note stay in Chinese.
