"""测试用的临时主目录。所有用例都在这里跑，不碰真实的 ~/.grok 和 ~/.grok-accounts。"""

import os
import shutil
import sys
import tempfile
from contextlib import contextmanager

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from multi_grok.platform import ISOLATION_ENV  # noqa: E402


@contextmanager
def isolated_home():
    tmp = tempfile.mkdtemp(prefix="multi-grok-test-")
    home = os.path.join(tmp, "home")
    xdg = os.path.join(tmp, "xdg")
    bin_dir = os.path.join(home, ".local", "bin")
    os.makedirs(bin_dir)
    os.makedirs(xdg)
    keys = ["HOME", "XDG_CONFIG_HOME", "PATH", "MULTI_GROK_TEST_OUTPUT"] + list(ISOLATION_ENV)
    saved = {key: os.environ.get(key) for key in keys}
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = xdg
    os.environ["PATH"] = bin_dir + os.pathsep + saved["PATH"] if saved["PATH"] else bin_dir
    for key in ISOLATION_ENV:
        os.environ.pop(key, None)
    try:
        yield {"tmp": tmp, "home": home, "xdg": xdg, "bin": bin_dir}
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp)


def write_executable(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o755)
