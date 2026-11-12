"""Private atomic JSON persistence shared by service state owners."""
import json
import os
from pathlib import Path
import tempfile


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.node-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        # State files can contain administrator hashes, SMTP credentials, or
        # encrypted account material. Keep them private on Unix hosts.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

