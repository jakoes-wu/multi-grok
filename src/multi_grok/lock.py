"""写操作互斥锁。

fcntl.flock 在进程退出（含被 SIGKILL）时由内核释放，不靠锁文件里的 PID 判断谁持有锁。
PID 只写在锁文件里，报错时告诉用户是哪个进程。
"""

import fcntl
import os
from typing import Optional

from . import platform


class LockBusyError(Exception):
    def __init__(self, holder_pid: Optional[str]) -> None:
        super().__init__(holder_pid)
        self.holder_pid = holder_pid


class WriteLock(object):
    def __init__(self) -> None:
        self.path = os.path.join(platform.state_dir(), "lock")
        self._fd: Optional[int] = None

    def __enter__(self) -> "WriteLock":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # 不能带 O_TRUNC：还没拿到锁就截断，会清掉持锁进程写下的 PID。
        # 也不能换 inode：两个进程会各自锁住不同的文件。
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            holder = os.read(fd, 64).decode("ascii", "replace").strip() or None
            os.close(fd)
            raise LockBusyError(holder)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._fd = fd
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
