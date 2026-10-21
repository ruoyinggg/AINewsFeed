"""Initialize mounted storage, then run Signal as an unprivileged user."""
import os
from pathlib import Path
import sys


def main():
    database = Path(os.environ.get('DATABASE_PATH', '/var/lib/signal/news.sqlite3'))
    database.parent.mkdir(parents=True, exist_ok=True)
    if os.getuid() == 0:
        # A newly mounted hosting disk may be root-owned. Only touch app storage.
        os.chown(database.parent, 10001, 10001)
        for path in (database, Path(str(database) + '-wal'), Path(str(database) + '-shm')):
            if path.exists():
                os.chown(path, 10001, 10001)
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)
    os.execv(sys.executable, [sys.executable, '/app/app.py'])


if __name__ == '__main__':
    main()
