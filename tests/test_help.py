"""方案 feature-help-examples §8：顶层分组帮助，以及每个子命令自己的 Examples。"""

import argparse
import unittest
from io import StringIO
from contextlib import redirect_stderr, redirect_stdout

import helpers  # noqa: F401  把 src 放进 sys.path，且不碰真实 HOME
from multi_grok import cli
from multi_grok.cli import run


def capture(argv):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = run(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def subcommand_names(parser):
    names = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.extend(action.choices)
    return names


class HelpTest(unittest.TestCase):
    def test_t1_grouped_help_covers_every_command(self):
        code, out, err = capture(["--help"])
        self.assertEqual(code, 0, err)
        for title in ("Getting started:", "Account settings:", "Everyday:", "Setup and checks:", "Examples:"):
            self.assertIn(title, out)
        parser = cli._parser()
        names = subcommand_names(parser)
        grouped = cli.grouped_command_names()
        self.assertEqual(len(grouped), len(set(grouped)))
        self.assertEqual(sorted(grouped), sorted(names))
        self.assertEqual(sorted(cli.COMMAND_HELP), sorted(names))
        self.assertEqual(sorted(cli.COMMAND_EXAMPLES), sorted(names))
        # 子命令不再平铺。分组列表是两个空格，argparse 平铺是四个空格。
        self.assertNotRegex(out, r"\n    init\s{2,}")
        self.assertNotIn("{init,", out)
        for command, text in cli.TOP_EXAMPLES:
            self.assertIn(command, out)
            if text:
                self.assertIn(text, out)

    def test_t2_every_subcommand_lists_its_examples(self):
        for name in subcommand_names(cli._parser()):
            with self.subTest(command=name):
                code, out, err = capture([name, "--help"])
                self.assertEqual(code, 0, err)
                self.assertIn(cli.COMMAND_HELP[name], out)
                self.assertIn("\nExamples:\n", out)
                listed = []
                in_examples = False
                for line in out.splitlines():
                    if line == "Examples:":
                        in_examples = True
                        continue
                    if in_examples and line.strip():
                        listed.append(line)
                self.assertEqual(tuple(item.strip() for item in listed), cli.COMMAND_EXAMPLES[name])
                for line in cli.COMMAND_EXAMPLES[name]:
                    self.assertTrue(line == "multi-grok {}".format(name) or line.startswith("multi-grok {} ".format(name)))


if __name__ == "__main__":
    unittest.main()
