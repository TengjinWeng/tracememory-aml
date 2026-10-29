"""Generate a separate candidate service secret without printing it."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
folder = root / "secrets"
folder.mkdir(mode=0o700, exist_ok=True)
path = folder / "candidate-service.env"
fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("CANDIDATE_API_KEY=" + secrets.token_urlsafe(36) + "\n")
    stream.write("CANDIDATE_ENABLE_MODELS=0\n")
print("Separate private candidate service key generated; model calls disabled.")
