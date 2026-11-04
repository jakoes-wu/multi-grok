"""v0.3 本地报表。临时 HOME，不碰真实的 ~/.grok。"""

import json
import os
import unittest
from datetime import datetime, timezone

import helpers
from multi_grok.cli import run


def capture(argv):
    from io import StringIO
    from contextlib import redirect_stderr, redirect_stdout
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = run(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def _summary(directory, session_id, updated, title, model="grok-test"):
    os.makedirs(directory)
    body = {
        "info": {"id": session_id, "cwd": "/tmp/proj"},
        "updated_at": updated,
        "current_model_id": model,
        "session_summary": title,
    }
    with open(os.path.join(directory, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(body, handle)


def _plant(directory, name, text):
    with open(os.path.join(directory, name), "w", encoding="utf-8") as handle:
        handle.write(text)


class ReportTest(unittest.TestCase):
    def test_sessions_list_unregistered_grok_home_as_default(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            home_session = os.path.join(env["home"], ".grok-accounts", "home", "sessions", "cwd", "reg")
            _summary(home_session, "reg", now, "registered title")
            default_session = os.path.join(env["home"], ".grok", "sessions", "cwd", "abc")
            _summary(default_session, "abc", now, "plain grok title")
            _plant(default_session, "updates.jsonl", "secret-conversation-line\n")
            code, out, err = capture(["sessions", "--cmd"])
            self.assertEqual(code, 0, err)
            self.assertIn("default", out)
            self.assertIn("plain grok title", out)
            self.assertIn("registered title", out)
            self.assertIn("grok --resume 'abc'", out)
            self.assertIn("grok-home --resume 'reg'", out)
            self.assertNotIn("grok-default", out)
            self.assertNotIn("secret-conversation-line", out + err)

    def test_registered_default_is_not_listed_twice(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "default"])[0], 0)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            registered = os.path.join(env["home"], ".grok-accounts", "default", "sessions", "cwd", "reg")
            _summary(registered, "reg", now, "registered default")
            plain = os.path.join(env["home"], ".grok", "sessions", "cwd", "abc")
            _summary(plain, "abc", now, "only-in-real-grok-home")
            code, out, err = capture(["sessions", "--cmd"])
            self.assertEqual(code, 0, err)
            self.assertIn("registered default", out)
            self.assertNotIn("only-in-real-grok-home", out + err)
            self.assertIn("grok-default --resume 'reg'", out)
            self.assertNotIn("&& grok --resume", out)

    def test_symlink_grok_home_is_not_walked(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            real = os.path.join(env["tmp"], "elsewhere")
            session = os.path.join(real, "sessions", "cwd", "abc")
            _summary(session, "abc", "2026-11-03T00:00:00Z", "hidden-behind-symlink")
            os.symlink(real, os.path.join(env["home"], ".grok"))
            code, out, err = capture(["sessions"])
            self.assertEqual(code, 0, err)
            self.assertNotIn("hidden-behind-symlink", out + err)
            self.assertIn("not a real directory", err)
            code, _out, err = capture(["sessions", "default"])
            self.assertEqual(code, 1)
            self.assertIn("not a real directory", err)

    def test_list_shows_the_latest_summary_time(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            base = os.path.join(env["home"], ".grok-accounts", "home", "sessions", "cwd")
            _summary(os.path.join(base, "old"), "old", "2026-11-01T00:00:00Z", "old")
            _summary(os.path.join(base, "new"), "new", "2026-11-03T03:04:05Z", "new")
            _summary(os.path.join(base, "bad"), "bad", "not-a-time", "bad")
            code, out, err = capture(["list"])
            self.assertEqual(code, 0, err)
            self.assertIn("last=2026-11-03T03:04:05Z", out)
            code, names, _err = capture(["list", "--names"])
            self.assertEqual(code, 0)
            self.assertEqual(names.strip(), "home")
            code, body, _err = capture(["list", "--json"])
            self.assertEqual(code, 0)
            listed = json.loads(body)
            self.assertEqual(listed["accounts"][0]["last"], "2026-11-03T03:04:05Z")
            code, _added, add_err = capture(["add", "work"])
            self.assertEqual(code, 0, add_err)
            code, out, _err = capture(["list"])
            self.assertIn("work", out)
            self.assertIn("last=-", out)

    def test_usage_history_sums_numbers_and_hides_the_ledger_text(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            self.assertEqual(capture(["args", "home", "--", "--minimal"])[0], 0)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            base = os.path.join(env["home"], ".grok-accounts", "home", "sessions", "cwd")
            one = os.path.join(base, "one")
            two = os.path.join(base, "two")
            old = os.path.join(base, "old")
            _summary(one, "one", now, "one")
            _summary(two, "two", now, "two")
            _summary(old, "old", "2019-01-01T00:00:00Z", "old")
            for path in (one, two, old):
                _plant(path, "updates.jsonl", "secret-from-jsonl\n")
                _plant(path, "signals.json", "secret-from-signals\n")
                _plant(path, "usage.json", "secret-from-usage-file\n")
            argv_path = os.path.join(env["tmp"], "argv")
            cwd_path = os.path.join(env["tmp"], "cwd")
            home_path = os.path.join(env["tmp"], "grok-home")
            env_path = os.path.join(env["tmp"], "xai")
            script = """#!/bin/sh
printf '%s\\n' "$@" >> "$MULTI_GROK_TEST_ARGV"
printf '%s\\n' "$PWD" > "$MULTI_GROK_TEST_CWD"
printf '%s\\n' "$GROK_HOME" > "$MULTI_GROK_TEST_HOME"
if [ -n "${XAI_API_KEY+x}" ]; then printf 'set\\n' > "$MULTI_GROK_TEST_ENV"
else printf 'unset\\n' > "$MULTI_GROK_TEST_ENV"; fi
id=$2
case "$id" in
  one)
    printf '%s\\n' '{"sessionId":"one","session":{"inputTokens":10,"outputTokens":4,"cacheCreationTokens":1,"reasoningTokens":2,"totalTokens":17,"modelCalls":1,"costUsdTicks":25000000000,"primaryModelId":"grok-test"},"turns":[{"endedAt":"2026-11-03T01:02:03Z","inputTokens":10,"outputTokens":4,"cacheCreationTokens":1,"reasoningTokens":2,"totalTokens":17,"modelCalls":1,"costUsdTicks":25000000000,"secret":"secret-turn-text"}]}'
    ;;
  two)
    printf '%s\\n' '{"sessionId":"two","session":{"inputTokens":7,"outputTokens":1,"totalTokens":8,"costUsdTicks":10000000000,"primaryModelId":"grok-test"},"turns":[{"endedAt":"2026-11-03T04:00:00Z","inputTokens":7,"outputTokens":1,"totalTokens":8,"costUsdTicks":10000000000}]}'
    ;;
  *)
    printf '%s\\n' '{"session":{}}'
    ;;
esac
"""
            helpers.write_executable(os.path.join(env["bin"], "grok"), script)
            os.environ["MULTI_GROK_TEST_ARGV"] = argv_path
            os.environ["MULTI_GROK_TEST_CWD"] = cwd_path
            os.environ["MULTI_GROK_TEST_HOME"] = home_path
            os.environ["MULTI_GROK_TEST_ENV"] = env_path
            os.environ["XAI_API_KEY"] = "secret-api-key"
            code, out, err = capture(["usage", "home", "--history", "--days", "7"])
            self.assertEqual(code, 0, err)
            self.assertIn("inputTokens=17", out)
            self.assertIn("outputTokens=5", out)
            self.assertIn("costUsd=3.5", out)
            self.assertIn("sessions=2", out)
            self.assertNotIn("secret-turn-text", out + err)
            self.assertNotIn("secret-from-jsonl", out + err)
            self.assertNotIn("secret-from-signals", out + err)
            self.assertNotIn("secret-from-usage-file", out + err)
            self.assertNotIn("secret-api-key", out + err)
            self.assertNotIn("--minimal", out + err)
            self.assertIn("working directory was not changed", err)
            recorded = open(argv_path, encoding="utf-8").read().splitlines()
            self.assertEqual(recorded, ["usage", "one", "usage", "two"])
            self.assertEqual(open(cwd_path, encoding="utf-8").read().strip(), os.getcwd())
            self.assertEqual(open(home_path, encoding="utf-8").read().strip(),
                             os.path.join(env["home"], ".grok-accounts", "home"))
            self.assertEqual(open(env_path, encoding="utf-8").read().strip(), "unset")

    def test_usage_history_by_model_and_failed_grok_hides_stdout(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            session = os.path.join(env["home"], ".grok-accounts", "home", "sessions", "cwd", "one")
            _summary(session, "one", now, "one")
            script = r"""#!/bin/sh
if [ "$2" = "one" ]; then
  printf '%s\n' '{"session":{"primaryModelId":"fallback","modelUsage":{"grok-a":{"inputTokens":3,"costUsdTicks":10000000000},"grok-b":{"inputTokens":4,"costUsdTicks":10000000000}}},"turns":[{"secret":"secret-turn-text"}]}'
  exit 0
fi
printf '%s\n' 'stdout-secret'
exit 9
"""
            helpers.write_executable(os.path.join(env["bin"], "grok"), script)
            code, out, err = capture(["usage", "home", "--history", "--by", "model", "--json"])
            self.assertEqual(code, 0, err)
            self.assertNotIn("secret-turn-text", out + err)
            body = json.loads(out)
            keys = [row["key"] for row in body["rows"]]
            self.assertEqual(keys, ["grok-a", "grok-b"])
            self.assertEqual(body["total"]["inputTokens"], 7)
            self.assertEqual(body["total"]["costUsd"], "2")
            self.assertTrue(body["cwdUnverified"])
            _summary(os.path.join(env["home"], ".grok-accounts", "home", "sessions", "other", "bad"),
                     "bad", now, "bad")
            code, out, err = capture(["usage", "home", "--history"])
            self.assertEqual(code, 1)
            self.assertIn("exited 9", err)
            self.assertNotIn("stdout-secret", out + err)
            self.assertNotIn("inputTokens=", out)

    def test_usage_without_history_still_forwards(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            self.assertEqual(capture(["args", "home", "--", "--minimal"])[0], 0)
            output = os.path.join(env["tmp"], "usage-out")
            # 子进程继承真实标准输出，不进 capture 的 StringIO。参数写到文件里核对。
            helpers.write_executable(
                os.path.join(env["bin"], "grok"),
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$MULTI_GROK_TEST_OUTPUT\"\n",
            )
            os.environ["MULTI_GROK_TEST_OUTPUT"] = output
            code, _out, err = capture(["usage", "home", "abc"])
            self.assertEqual(code, 0, err)
            with open(output, encoding="utf-8") as handle:
                forwarded = handle.read()
            self.assertEqual(forwarded.splitlines(), ["usage", "abc"])
            code, _out, err = capture(["usage", "home", "--history", "abc"])
            self.assertEqual(code, 2, err)
            code, _out, err = capture(["usage", "home", "abc", "--days", "1"])
            self.assertEqual(code, 2, err)

    def test_usage_history_reads_unregistered_grok_home(self):
        with helpers.isolated_home() as env:
            self.assertEqual(capture(["add", "home"])[0], 0)
            self.assertEqual(capture(["args", "home", "--", "--minimal"])[0], 0)
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            session = os.path.join(env["home"], ".grok", "sessions", "cwd", "abc")
            _summary(session, "abc", now, "plain")
            _plant(session, "updates.jsonl", "secret-from-jsonl\n")
            argv_path = os.path.join(env["tmp"], "argv")
            home_path = os.path.join(env["tmp"], "seen-home")
            helpers.write_executable(
                os.path.join(env["bin"], "grok"),
                "#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$MULTI_GROK_TEST_ARGV\"\n"
                "printf '%s\\n' \"$GROK_HOME\" > \"$MULTI_GROK_TEST_HOME\"\n"
                "printf '%s\\n' '{\"session\":{\"inputTokens\":6,\"costUsdTicks\":10000000000}}'\n",
            )
            os.environ["MULTI_GROK_TEST_ARGV"] = argv_path
            os.environ["MULTI_GROK_TEST_HOME"] = home_path
            code, out, err = capture(["usage", "default", "--history"])
            self.assertEqual(code, 0, err)
            self.assertIn("inputTokens=6", out)
            self.assertNotIn("secret-from-jsonl", out + err)
            self.assertNotIn("--minimal", open(argv_path, encoding="utf-8").read())
            self.assertEqual(open(home_path, encoding="utf-8").read().strip(),
                             os.path.join(env["home"], ".grok"))
            code, out, err = capture(["usage", "--history"])
            self.assertEqual(code, 0, err)
            self.assertIn("default  by=day", out)
            self.assertIn("home  by=day", out)
            self.assertIn("no usage", out)

    def test_doctor_warns_when_path_hides_the_launcher(self):
        with helpers.isolated_home() as env:
            helpers.write_executable(os.path.join(env["bin"], "grok"), "#!/bin/sh\nexit 0\n")
            self.assertEqual(capture(["add", "home"])[0], 0)
            code, out, err = capture(["doctor"])
            self.assertEqual(code, 0, err)
            self.assertIn("ok", out)
            self.assertNotIn("hides", err)
            shadow = os.path.join(env["tmp"], "shadow")
            helpers.write_executable(os.path.join(shadow, "grok-home"), "#!/bin/sh\necho other\n")
            os.environ["PATH"] = shadow + os.pathsep + os.environ["PATH"]
            code, out, err = capture(["doctor"])
            self.assertEqual(code, 0, err)
            self.assertIn("ok", out)
            self.assertIn("comes first on PATH and hides", err)
            self.assertIn(os.path.join(shadow, "grok-home"), err)
            self.assertTrue(os.path.isfile(os.path.join(env["bin"], "grok-home")))

    def test_completion_lists_history(self):
        with helpers.isolated_home():
            code, out, err = capture(["completion", "bash"])
            self.assertEqual(code, 0, err)
            self.assertIn("--history", out)
