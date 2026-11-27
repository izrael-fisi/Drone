"""Exclude a running gateway while copying its state directory."""

import os
from pathlib import Path


class StateLock:
    def __init__(self, state):
        path = Path(state) / ".gateway.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        self.file = os.fdopen(descriptor, "r+b")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError("The gateway state is in use. Stop the gateway before opening this directory.") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.file.close()
