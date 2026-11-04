import json
import unittest

import helpers
from multi_grok.config import ConfigError, normalize_proxy, parse_config, validate_env_key, validate_name


class ConfigTest(unittest.TestCase):
    def test_name_accepts_home(self):
        validate_name("home")

    def test_name_rejects_dot_and_slash(self):
        with self.assertRaises(ValueError):
            validate_name(".home")
        with self.assertRaises(ValueError):
            validate_name("a/b")

    def test_proxy_port_and_rejects_secrets_and_socks(self):
        self.assertEqual(normalize_proxy("7890"), "http://127.0.0.1:7890")
        self.assertEqual(normalize_proxy("inherit"), "inherit")
        with self.assertRaises(ValueError):
            normalize_proxy("http://user:pw@127.0.0.1:7890")
        with self.assertRaises(ValueError):
            normalize_proxy("socks5://127.0.0.1:1080")
        with self.assertRaises(ValueError):
            normalize_proxy("0")

    def test_env_rejects_isolation_and_credentials(self):
        for key in ("GROK_HOME", "XAI_API_KEY", "GROK_AUTH_PATH", "HTTPS_PROXY", "SOME_TOKEN"):
            with self.assertRaises(ValueError):
                validate_env_key(key)
        validate_env_key("GROK_MAX_PARALLEL_IMAGE_GEN_CALLS")

    def test_duplicate_case_is_rejected(self):
        raw = json.dumps({"version": 1, "accounts": {"home": {}, "Home": {}}})
        with self.assertRaises(ConfigError):
            parse_config(raw, "fixture")

    def test_unknown_shared_key_is_rejected(self):
        raw = json.dumps({"version": 1, "shared": {"dir": "~/.grok-shared"}})
        with self.assertRaises(ConfigError) as caught:
            parse_config(raw, "fixture")
        self.assertIn("shared", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
