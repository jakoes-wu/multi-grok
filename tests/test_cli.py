import json
import os
import stat
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout
import unittest

import helpers
from multi_grok.cli import run
from multi_grok.config import config_path


SECRET = "super-secret-token-value"


def capture(argv):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = run(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


class CliTest(unittest.TestCase):
    def test_add_list_login_and_remove_keep_the_directory(self):
        with helpers.isolated_home() as env:
            code, out, err = capture(["add", "home"])
            self.assertEqual(code, 0, err)
            self.assertIn("create", out)
            account = os.path.join(env["home"], ".grok-accounts", "home")
            launcher = os.path.join(env["bin"], "grok-home")
            self.assertEqual(stat.S_IMODE(os.lstat(account).st_mode), 0o700)
            self.assertTrue(os.path.isfile(launcher))
            isolate = open(os.path.join(account, "config.toml"), encoding="utf-8").read()
            self.assertIn("skills = false", isolate)
            self.assertIn("# managed-by: multi-grok account=home", isolate)

            code, out, _err = capture(["add", "home"])
            self.assertEqual(code, 0)
            self.assertNotIn("create", out)

            auth = os.path.join(account, "auth.json")
            with open(auth, "w", encoding="utf-8") as handle:
                handle.write('{"token":"%s"}\n' % SECRET)
            code, out, err = capture(["list"])
            self.assertEqual(code, 0)
            self.assertIn("launcher=ok", out)
            bare_code, bare_out, _bare_err = capture([])
            self.assertEqual(bare_code, 0)
            self.assertEqual(bare_out, out)
            self.assertIn("login=file", out)
            self.assertIn("isolate=managed", out)
            self.assertNotIn(SECRET, out + err)

            output = os.path.join(env["tmp"], "login-out")
            helpers.write_executable(os.path.join(env["bin"], "grok"), FAKE)
            os.environ["XAI_API_KEY"] = SECRET
            os.environ["MULTI_GROK_TEST_OUTPUT"] = output
            code = run(["login", "home", "--", "--device-auth"])
            self.assertEqual(code, 0)
            body = open(output, encoding="utf-8").read()
            self.assertIn("GROK_HOME={}".format(account), body)
            self.assertIn("XAI=unset", body)
            self.assertIn("args: login --device-auth", body)
            self.assertNotIn(SECRET, body)

            code, _out, err = capture(["doctor"])
            self.assertEqual(code, 0, err)
            self.assertNotIn(SECRET, err)

            code, _out, err = capture(["remove", "home"])
            self.assertEqual(code, 0, err)
            self.assertFalse(os.path.exists(launcher))
            self.assertTrue(os.path.isfile(auth))
            self.assertIn(SECRET, open(auth, encoding="utf-8").read())

    def test_unmanaged_launcher_blocks_every_write(self):
        with helpers.isolated_home() as env:
            path = os.path.join(env["bin"], "grok-home")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("#!/bin/sh\necho mine\n")
            code, _out, err = capture(["add", "home"])
            self.assertEqual(code, 3)
            self.assertIn("nothing was written", err)
            self.assertFalse(os.path.exists(config_path()))
            self.assertIn("echo mine", open(path, encoding="utf-8").read())
            self.assertFalse(os.path.exists(os.path.join(env["home"], ".grok-accounts", "home")))

    def test_user_config_toml_is_not_overwritten(self):
        with helpers.isolated_home() as env:
            account = os.path.join(env["home"], ".grok-accounts", "home")
            os.makedirs(account, 0o700)
            path = os.path.join(account, "config.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("owned = true\n")
            code, out, _err = capture(["add", "home"])
            self.assertEqual(code, 0)
            self.assertIn("left untouched", out)
            self.assertEqual(open(path, encoding="utf-8").read(), "owned = true\n")
            _code, listed, _err = capture(["list"])
            self.assertIn("isolate=user", listed)

    def test_refuses_to_nest_inside_default_grok_home(self):
        with helpers.isolated_home():
            code, _out, err = capture(["init", "--root", "~/.grok"])
            self.assertEqual(code, 0, err)
            code, _out, err = capture(["add", "home"])
            self.assertEqual(code, 3, err)
            self.assertIn("~/.grok", err)
            raw = open(config_path(), encoding="utf-8").read()
            self.assertNotIn('"home"', raw)

    def test_env_and_args_and_proxy(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home", "--proxy", "7890"])[0], 0)
            code, _out, err = capture(["env", "--defaults", "LANG=C"])
            self.assertEqual(code, 0, err)
            code, _out, err = capture(["env", "home", "XAI_API_KEY=nope"])
            self.assertEqual(code, 2, err)
            code, _out, err = capture(["args", "home", "--", "--minimal"])
            self.assertEqual(code, 0, err)
            text = open(os.path.join(env["bin"], "grok-home"), encoding="utf-8").read()
            self.assertIn("HTTPS_PROXY=http://127.0.0.1:7890", text)
            self.assertIn("LANG=C", text)
            self.assertIn("exec grok --minimal", text)

    def test_dry_run_writes_nothing(self):
        with helpers.isolated_home() as env:
            code, out, _err = capture(["add", "home", "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn("dry-run", out)
            self.assertFalse(os.path.exists(os.path.join(env["home"], ".grok-accounts")))
            self.assertFalse(os.path.exists(config_path()))

    def test_doctor_on_empty_home_is_ok(self):
        with helpers.isolated_home() as env:
            # CI 上没有 grok。放一个什么都不做的可执行文件，只验证 doctor 认 PATH。
            helpers.write_executable(os.path.join(env["bin"], "grok"), "#!/bin/sh\nexit 0\n")
            code, out, err = capture(["doctor"])
            self.assertEqual(code, 0, err)
            self.assertIn("ok", out)

    def test_apply_file_can_add_and_then_drop_an_account(self):
        with helpers.isolated_home() as env:
            path = os.path.join(env["tmp"], "accounts.json")
            body = {
                "version": 1,
                "root": "~/.grok-accounts",
                "bin_dir": "~/.local/bin",
                "accounts": {"home": {"proxy": "off", "isolate": False}},
            }
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(body))
            code, _out, err = capture(["apply", "-f", path])
            self.assertEqual(code, 0, err)
            account = os.path.join(env["home"], ".grok-accounts", "home")
            self.assertTrue(os.path.isdir(account))
            self.assertFalse(os.path.exists(os.path.join(account, "config.toml")))
            text = open(os.path.join(env["bin"], "grok-home"), encoding="utf-8").read()
            self.assertIn("unset HTTPS_PROXY", text)
            body["accounts"] = {}
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(body))
            code, _out, err = capture(["apply", "-f", path])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.isdir(account))
            self.assertFalse(os.path.exists(os.path.join(env["bin"], "grok-home")))


FAKE = """#!/bin/sh
{
  printf 'GROK_HOME=%s\\n' "$GROK_HOME"
  printf 'XAI=%s\\n' "${XAI_API_KEY-unset}"
  printf 'args:'
  printf ' %s' "$@"
  printf '\\n'
} > "$MULTI_GROK_TEST_OUTPUT"
"""


if __name__ == "__main__":
    unittest.main()
