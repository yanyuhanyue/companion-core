"""Independent SQLite writer for the bounded HTTP isolation test."""

import sys

from companion.store import Store

store = Store(sys.argv[1])
try:
    with store.transaction():
        print("LOCKED", flush=True)
        assert sys.stdin.readline() == "release\n"
finally:
    store.close()
