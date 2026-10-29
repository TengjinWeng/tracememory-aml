"""Prepare a private ngrok configuration; optionally deploy and start on 106."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/data/wengtengjin/tracememory-aml"
HOST = "gmlab_out_106"


def write_config(token):
    folder = ROOT / "secrets"
    folder.mkdir(mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    path = folder / "ngrok-config.json"
    payload = {"version": "3", "agent": {
        "authtoken": token, "web_addr": "127.0.0.1:4042",
        "log": "stdout", "log_format": "json", "update_check": False,
        "remote_management": False,
    }}
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream)
        stream.write("\n")
    return path


def ssh(command):
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
                             HOST, command], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise SystemExit("Remote operation did not complete; details withheld to avoid exposing credentials")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--start", action="store_true", help="also deploy, then start a dedicated tmux session")
    args = parser.parse_args()
    token_file = ROOT / "secrets/ngrok-token.txt"
    if not token_file.exists():
        raise SystemExit("Save the ngrok authtoken to secrets/ngrok-token.txt first")
    os.chmod(token_file, 0o600)
    token = token_file.read_text().strip()
    if "PASTE_" in token or len(token) < 20 or any(x.isspace() for x in token):
        raise SystemExit("ngrok token is not configured. No account connection or deployment attempted.")
    config = write_config(token)
    print("Private config prepared (0600). No token displayed.")
    if args.deploy or args.start:
        ssh("umask 077; mkdir -p " + REMOTE + "/secrets; chmod 700 " + REMOTE + "/secrets")
        result = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(config),
                                 HOST + ":" + REMOTE + "/secrets/ngrok-config.json"],
                                capture_output=True, timeout=30)
        if result.returncode:
            raise SystemExit("Could not transfer private configuration")
        ssh("cd " + REMOTE + " && chmod 600 secrets/ngrok-config.json && "
            "tools/ngrok config check --config secrets/ngrok-config.json")
        print("Remote configuration parsed successfully. No tunnel started by config check.")
    if args.start:
        # Existing sessions are not replaced; never disconnect another endpoint silently.
        ssh("cd " + REMOTE + " && ! tmux has-session -t tracememory-ngrok 2>/dev/null && "
            "tmux new-session -d -s tracememory-ngrok "
            "'cd " + REMOTE + " && sh scripts/supervise_ngrok.sh'")
        print("Dedicated ngrok session started. Public connectivity must still be verified.")
        print("Official application, Smoke and Full have NOT been submitted.")


if __name__ == "__main__":
    main()
