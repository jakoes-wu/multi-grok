import os
import stat
import subprocess
import unittest

import helpers
from multi_grok.launcher import render, shell_account_dir, trailing_option_warning


FAKE_GROK = """#!/bin/sh
{
  printf 'GROK_HOME=%s\\n' "$GROK_HOME"
  printf 'XAI=%s\\n' "${XAI_API_KEY-unset}"
  printf 'AUTH=%s\\n' "${GROK_AUTH-unset}"
  printf 'CFG=%s\\n' "${GROK_CONFIG-unset}"
  printf 'HTTPS=%s\\n' "${HTTPS_PROXY-unset}"
  printf 'args:'
  printf ' %s' "$@"
  printf '\\n'
} > "$MULTI_GROK_TEST_OUTPUT"
"""


class LauncherTest(unittest.TestCase):
    def test_default_dir_follows_home(self):
        with helpers.isolated_home() as env:
            expr = shell_account_dir("~/.grok-accounts", "home")
            self.assertEqual(expr, '"$HOME/.grok-accounts/home"')
            text = render("home", "~/.grok-accounts", "http://127.0.0.1:7890", {"LANG": "C"}, ["--minimal"])
            self.assertIn("# managed-by: multi-grok account=home", text)
            self.assertIn('GROK_HOME="$account_dir"', text)
            self.assertIn("unset XAI_API_KEY GROK_CODE_XAI_API_KEY GROK_AUTH GROK_AUTH_PATH "
                          "GROK_CONFIG GROK_CONFIG_PATH", text)
            self.assertIn("exec grok --minimal \"$@\"", text)
            self.assertNotIn(env["home"], text.split("account_dir=", 1)[1].split("\n", 1)[0])

    def test_warns_when_last_fixed_option_takes_a_value(self):
        self.assertIsNotNone(trailing_option_warning(["--resume"]))
        self.assertIsNone(trailing_option_warning(["--resume", "abc"]))
        self.assertIsNone(trailing_option_warning(["--minimal"]))

    def test_script_sets_home_and_strips_credentials(self):
        with helpers.isolated_home() as env:
            account = os.path.join(env["home"], ".grok-accounts", "home")
            os.makedirs(account, 0o700)
            output = os.path.join(env["tmp"], "out")
            helpers.write_executable(os.path.join(env["bin"], "grok"), FAKE_GROK)
            script = os.path.join(env["tmp"], "grok-home")
            helpers.write_executable(script, render("home", "~/.grok-accounts", "inherit", {}, []))
            self.assertTrue(stat.S_ISREG(os.lstat(script).st_mode))
            syntax = subprocess.call(["sh", "-n", script])
            self.assertEqual(syntax, 0)
            os.environ["XAI_API_KEY"] = "secret-key"
            os.environ["GROK_AUTH"] = "secret-auth"
            os.environ["GROK_CONFIG"] = "{\"models\":{}}"
            os.environ["MULTI_GROK_TEST_OUTPUT"] = output
            completed = subprocess.run([script, "hello"], check=False)
            self.assertEqual(completed.returncode, 0)
            body = open(output, encoding="utf-8").read()
            self.assertIn("GROK_HOME={}".format(account), body)
            self.assertIn("XAI=unset", body)
            self.assertIn("AUTH=unset", body)
            self.assertIn("CFG=unset", body)
            self.assertIn("args: hello", body)

    def test_script_refuses_symlink_without_running_grok(self):
        with helpers.isolated_home() as env:
            root = os.path.join(env["home"], ".grok-accounts")
            target = os.path.join(env["tmp"], "real")
            os.makedirs(target, 0o700)
            os.makedirs(root, 0o700)
            os.symlink(target, os.path.join(root, "home"))
            output = os.path.join(env["tmp"], "out")
            helpers.write_executable(os.path.join(env["bin"], "grok"), FAKE_GROK)
            script = os.path.join(env["tmp"], "grok-home")
            helpers.write_executable(script, render("home", "~/.grok-accounts", "off", {}, []))
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"
            os.environ["MULTI_GROK_TEST_OUTPUT"] = output
            completed = subprocess.run([script], check=False, stderr=subprocess.PIPE, text=True)
            self.assertEqual(completed.returncode, 1)
            self.assertIn("not a symlink", completed.stderr)
            self.assertFalse(os.path.exists(output))


if __name__ == "__main__":
    unittest.main()
