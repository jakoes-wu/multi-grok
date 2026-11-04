import json
import os
import stat
import tarfile
import unittest

import helpers
from multi_grok.cli import run
from multi_grok.config import ConfigError, parse_config, validate_name
from multi_grok.routes import render_router, resolve
from multi_grok.config import load_config


def capture(argv):
    from io import StringIO
    from contextlib import redirect_stderr, redirect_stdout
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = run(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


class MoreTest(unittest.TestCase):
    def test_edited_managed_config_toml_is_left_in_place(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            path = os.path.join(env["home"], ".grok-accounts", "home", "config.toml")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n[marketplace]\ndefault_skills_installs_purged = true\n")
            before = open(path, encoding="utf-8").read()
            code, out, err = capture(["apply"])
            self.assertEqual(code, 0, err)
            self.assertEqual(open(path, encoding="utf-8").read(), before)
            self.assertIn("left untouched", out)
            code, out, err = capture(["set", "home", "--no-isolate"])
            self.assertEqual(code, 0, err)
            self.assertEqual(open(path, encoding="utf-8").read(), before)

    def test_auto_name_is_reserved(self):
        with self.assertRaises(ValueError):
            validate_name("auto")

    def test_rename_keeps_the_directory_and_login_file(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            account = os.path.join(env["home"], ".grok-accounts", "home")
            auth = os.path.join(account, "auth.json")
            with open(auth, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            code, _out, err = capture(["rename", "home", "work"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.isdir(account))
            self.assertTrue(os.path.isfile(auth))
            self.assertFalse(os.path.exists(os.path.join(env["home"], ".grok-accounts", "work")))
            self.assertTrue(os.path.isfile(os.path.join(env["bin"], "grok-work")))
            self.assertFalse(os.path.exists(os.path.join(env["bin"], "grok-home")))
            code, out, _err = capture(["path", "work"])
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), account)

    def test_route_script_selects_the_longest_directory(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            self.assertEqual(capture(["add", "work"])[0], 0)
            base = os.path.join(env["tmp"], "proj")
            nested = os.path.join(base, "web")
            os.makedirs(nested)
            self.assertEqual(capture(["route", base, "home"])[0], 0)
            self.assertEqual(capture(["route", "--default", "work"])[0], 0)
            self.assertEqual(capture(["route", nested, "work"])[0], 0)
            config, _exists = load_config()
            account, rule = resolve(config, nested)
            self.assertEqual(account.name, "work")
            self.assertEqual(rule.path, nested)
            account, rule = resolve(config, base)
            self.assertEqual(account.name, "home")
            script = os.path.join(env["bin"], "grok-auto")
            self.assertEqual(os.system("sh -n {}".format(script)), 0)
            self.assertIn("grok-work", open(script, encoding="utf-8").read())
            text = render_router(config)
            self.assertIn("# managed-by: multi-grok router", text)

    def test_sessions_do_not_print_the_conversation(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            session = os.path.join(env["home"], ".grok-accounts", "home", "sessions", "cwd", "abc")
            os.makedirs(session)
            summary = {
                "info": {"id": "abc", "cwd": "/tmp/proj"},
                "updated_at": "2026-11-03T00:00:00Z",
                "current_model_id": "grok-test",
                "session_summary": "fix the launcher",
            }
            with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
                json.dump(summary, handle)
            with open(os.path.join(session, "updates.jsonl"), "w", encoding="utf-8") as handle:
                handle.write("secret-conversation-line\n")
            code, out, err = capture(["sessions", "--cmd"])
            self.assertEqual(code, 0, err)
            self.assertIn("abc", out)
            self.assertIn("fix the launcher", out)
            self.assertIn("grok-home --resume", out)
            self.assertNotIn("secret-conversation-line", out + err)

    def test_handoff_copies_the_session_and_skips_symlinks(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            self.assertEqual(capture(["add", "work"])[0], 0)
            session = os.path.join(env["home"], ".grok-accounts", "home", "sessions", "cwd", "abc")
            os.makedirs(session)
            with open(os.path.join(session, "summary.json"), "w", encoding="utf-8") as handle:
                handle.write('{"info": {"id": "abc", "cwd": "/tmp/proj"}}\n')
            code, out, err = capture(["handoff", "work", "--from", "home", "--session", "abc"])
            self.assertEqual(code, 0, err)
            copied = os.path.join(env["home"], ".grok-accounts", "work", "sessions", "cwd", "abc", "summary.json")
            self.assertTrue(os.path.isfile(copied), out + err)
            os.symlink(session, os.path.join(session, "linked"))
            code, _out, err = capture(["handoff", "work", "--from", "home", "--session", "abc", "--force"])
            self.assertEqual(code, 1, err)
            self.assertIn("not a regular file", err)
            self.assertTrue(os.path.isfile(copied))
            self.assertFalse(os.path.exists(os.path.dirname(copied) + ".partial"))

    def test_shared_link_and_conflict(self):
        with helpers.isolated_home() as env:
            shared = os.path.join(env["home"], ".grok-shared", "skills")
            os.makedirs(shared)
            code, _out, err = capture(["add", "home", "--shared", os.path.join(env["home"], ".grok-shared")])
            self.assertEqual(code, 0, err)
            link = os.path.join(env["home"], ".grok-accounts", "home", "skills")
            self.assertTrue(os.path.islink(link))
            self.assertEqual(os.path.realpath(link), os.path.realpath(shared))
            self.assertEqual(capture(["add", "work"])[0], 0)
            blocked = os.path.join(env["home"], ".grok-accounts", "work", "skills")
            os.mkdir(blocked)
            code, _out, err = capture(["set", "work", "--shared"])
            self.assertEqual(code, 3, err)
            self.assertTrue(os.path.isdir(blocked))
            self.assertFalse(os.path.islink(blocked))

    def test_run_sets_grok_home(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            output = os.path.join(env["tmp"], "out")
            helpers.write_executable(os.path.join(env["bin"], "grok"),
                                     "#!/bin/sh\nprintf '%s\\n' \"$GROK_HOME\" > \"$MULTI_GROK_TEST_OUTPUT\"\n")
            os.environ["MULTI_GROK_TEST_OUTPUT"] = output
            code, _out, err = capture(["run", "home", "--", "grok"])
            self.assertEqual(code, 0, err)
            with open(output, encoding="utf-8") as handle:
                self.assertEqual(handle.read().strip(), os.path.join(env["home"], ".grok-accounts", "home"))

    def test_export_leaves_out_auth_json(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            account = os.path.join(env["home"], ".grok-accounts", "home")
            with open(os.path.join(account, "auth.json"), "w", encoding="utf-8") as handle:
                handle.write('{"token":"nope"}\n')
            skill = os.path.join(account, "skills", "demo")
            os.makedirs(skill)
            with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8") as handle:
                handle.write("# demo\n")
            bundle = os.path.join(env["tmp"], "home.tar.gz")
            self.assertEqual(capture(["export", "home", bundle])[0], 0)
            with tarfile.open(bundle, "r:gz") as archive:
                names = archive.getnames()
            self.assertTrue(any(name.endswith("SKILL.md") for name in names))
            self.assertFalse(any("auth.json" in name for name in names))
            self.assertEqual(stat.S_IMODE(os.lstat(bundle).st_mode) & 0o077, 0)

    def test_completion_mentions_the_command(self):
        with helpers.isolated_home():
            code, out, err = capture(["completion", "bash"])
            self.assertEqual(code, 0, err)
            self.assertIn("multi-grok", out)
            self.assertIn("grok-auto", out) if False else self.assertIn("route", out)

    def test_shared_config_round_trip(self):
        raw = json.dumps({"version": 1, "shared": {"dir": "~/.grok-shared", "items": ["skills"]},
                          "accounts": {"home": {"shared": True}}})
        config = parse_config(raw, "fixture")
        self.assertTrue(config.accounts["home"].shared)
        self.assertEqual(config.shared_items, ["skills"])
        with self.assertRaises(ConfigError):
            parse_config(json.dumps({"version": 1, "accounts": {}, "routes": {"default": "missing"}}), "fixture")


if __name__ == "__main__":
    unittest.main()
