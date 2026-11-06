"""docs/feature/feature-completion-values.md §8：补全取值与 conversations 别名的回归（C0–C7）。"""

import argparse
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

import helpers
from multi_grok import completion
from multi_grok.cli import run
from multi_grok.config import config_path
from test_reports import _summary


def capture(argv):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = run(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def script(shell):
    code, out, err = capture(["completion", shell])
    assert code == 0, err
    return out


class ValueSpecTest(unittest.TestCase):
    def test_multi_value_positional(self):
        # C0
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest="command")
        sub = subs.add_parser("demo")
        sub.add_argument("items", nargs="*")
        sub.add_argument("mode", choices=["a", "b"])
        positional, _options = completion.value_spec(parser)
        self.assertNotIn("demo:1", positional)
        self.assertFalse(any(key.startswith("demo:") for key in positional))
        bad = argparse.ArgumentParser()
        bad.add_subparsers(dest="command").add_parser("demo").add_argument("items", nargs="*", choices=["a"])
        with self.assertRaises(ValueError):
            completion.value_spec(bad)


class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.ctx = helpers.isolated_home()
        self.env = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_bash_tables(self):
        # C1
        text = script("bash")
        for piece in ("day model", "off basic strict", "kind=shared", 'clash) candidates="auto off"'):
            self.assertIn(piece, text)
        self.assertIn("netcheck:1)", text)
        self.assertIn("usage:--by)", text)
        self.assertIn("netcheck:--clash-tun)", text)

    def test_syntax(self):
        # C3
        for shell, flag in (("bash", "-n"), ("zsh", "-n"), ("fish", "-n")):
            program = shutil.which(shell)
            if not program:
                continue
            path = os.path.join(self.env["tmp"], "c." + shell)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(script(shell))
            proc = subprocess.run([program, flag, path], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(proc.returncode, 0, (shell, proc.stderr))

    def test_fish_lines(self):
        # C4
        lines = script("fish").splitlines()
        clash = [line for line in lines if "subcommand_from netcheck" in line and "-l clash " in line + " "]
        self.assertTrue(clash)
        self.assertIn("-x", clash[0])
        self.assertIn("-a 'on off'", clash[0])
        self.assertNotIn(" -r", clash[0])
        shared = [line for line in lines if "-l shared-exclude" in line]
        self.assertTrue(shared)
        self.assertTrue(all("--list-shared-items" in line for line in shared))

    def test_list_shared_items(self):
        # C5
        code, out, _err = capture(["completion", "--list-shared-items"])
        self.assertEqual((code, out.split()), (0, ["skills", "commands", "agents"]))
        os.makedirs(os.path.dirname(config_path()), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as handle:
            handle.write("{broken")
        self.assertEqual(capture(["completion", "--list-shared-items"])[:2], (0, ""))

    def test_usage_errors_and_hidden_option(self):
        # C6
        self.assertEqual(capture(["completion"])[0], 2)
        self.assertEqual(capture(["completion", "bash", "--list-shared-items"])[0], 2)
        code, out, _err = capture(["completion", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("bash", out)
        self.assertNotIn("list-shared", out)
        bash = [line for line in script("bash").splitlines() if line.strip().startswith("completion)")]
        zsh = [line for line in script("zsh").splitlines() if line.strip().startswith("completion)")]
        fish = [line for line in script("fish").splitlines() if "subcommand_from completion" in line]
        for line in bash + zsh + fish:
            self.assertNotIn("list-shared", line)

    def test_conversations_alias(self):
        # C7
        from datetime import datetime, timezone
        self.assertEqual(capture(["add", "home"])[0], 0)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _summary(os.path.join(self.env["home"], ".grok-accounts", "home", "sessions", "cwd", "s1"), "s1", now, "t1")
        self.assertEqual(capture(["conversations"]), capture(["sessions"]))
        self.assertIn("t1", capture(["conversations"])[1])
        from multi_grok.cli import _resolve_command_alias
        self.assertEqual(_resolve_command_alias(["-x", "conversations", "a"]), ["-x", "sessions", "a"])
        self.assertEqual(_resolve_command_alias(["sessions", "conversations"]), ["sessions", "conversations"])
        code, _out, err = capture(["sessions", "conversations"])
        self.assertNotEqual(code, 0)
        self.assertIn("conversations", err)


@unittest.skipUnless(shutil.which("bash"), "bash not installed")
class BashRunTest(unittest.TestCase):
    """C2：在 bash 里实跑补全函数。PATH 最前面放指向源码的 multi-grok，不用本机已装的版本。"""

    def setUp(self):
        self.ctx = helpers.isolated_home()
        self.env = self.ctx.__enter__()
        helpers.write_executable(os.path.join(self.env["bin"], "multi-grok"),
                                 "#!/bin/sh\nPYTHONPATH={} exec {} -m multi_grok \"$@\"\n".format(
                                     os.path.join(helpers.ROOT, "src"), sys.executable))
        self.assertEqual(capture(["add", "home"])[0], 0)
        self.path = os.path.join(self.env["tmp"], "c.bash")
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(script("bash"))

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def complete(self, line):
        body = 'source "$1"; COMP_LINE="$2"; COMP_POINT=${#COMP_LINE}; _multi_grok; printf "%s\\n" "${COMPREPLY[@]}"'
        proc = subprocess.run(["bash", "-c", body, "bash", self.path, line], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return sorted(proc.stdout.decode("utf-8").split())

    def test_values(self):
        self.assertEqual(self.complete("multi-grok usage --by "), ["day", "model"])
        self.assertEqual(self.complete("multi-grok clash-api "), ["auto", "off"])
        self.assertEqual(self.complete("multi-grok netcheck home "), ["basic", "off", "strict"])
        self.assertEqual(self.complete("multi-grok netcheck home --clash "), ["off", "on"])
        self.assertEqual(self.complete("multi-grok proxy home "), ["inherit", "off"])
        self.assertEqual(self.complete("multi-grok add x --shared-exclude "), ["agents", "commands", "skills"])


if __name__ == "__main__":
    unittest.main()
