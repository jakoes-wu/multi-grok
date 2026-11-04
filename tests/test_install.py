import os
import subprocess
import unittest

import helpers


class InstallTest(unittest.TestCase):
    def test_help_and_install_and_uninstall(self):
        with helpers.isolated_home() as env:
            script = os.path.join(helpers.ROOT, "install.sh")
            help_run = subprocess.run([script, "-h"], check=False, stdout=subprocess.PIPE, text=True)
            self.assertEqual(help_run.returncode, 0)
            self.assertIn("--prefix", help_run.stdout)
            prefix = os.path.join(env["tmp"], "prefix")
            installed = subprocess.run([script, "--prefix", prefix], check=False,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            wrapper = os.path.join(prefix, "bin", "multi-grok")
            self.assertTrue(os.path.isfile(wrapper))
            version = subprocess.run([wrapper, "--version"], check=False,
                                     stdout=subprocess.PIPE, text=True)
            self.assertEqual(version.returncode, 0, version.stdout)
            self.assertIn("multi-grok 0.2.0", version.stdout)
            again = subprocess.run([script, "--prefix", prefix], check=False,
                                   stdout=subprocess.PIPE, text=True)
            self.assertEqual(again.returncode, 0, again.stdout)
            self.assertIn("unchanged", again.stdout)
            removed = subprocess.run([script, "--prefix", prefix, "--uninstall"], check=False,
                                     stdout=subprocess.PIPE, text=True)
            self.assertEqual(removed.returncode, 0, removed.stdout)
            self.assertFalse(os.path.exists(wrapper))
            self.assertIn("removed", removed.stdout)


if __name__ == "__main__":
    unittest.main()
