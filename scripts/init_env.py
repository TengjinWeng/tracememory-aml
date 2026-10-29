"""Create a task-specific secret locally; never prints the key."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env"
fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("MEMORY_API_KEY=" + secrets.token_urlsafe(36) + "\n")
    stream.write("MEMORY_HOST=127.0.0.1\nMEMORY_PORT=18086\nMEMORY_DB=data/memory.sqlite3\n")
print("Created private .env; no key printed. Existing files are never overwritten.")
