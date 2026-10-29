"""Prepare a private AML request draft; never submits registration or starts jobs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def write_private(path, payload):
    # The output may contain the memory-service credential. No stdout dump.
    fd = os.open(str(path), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-email", action="store_true", help="Read-only eligibility check; does not apply")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    info = json.loads((root / "docs/私人报名信息.json").read_text())
    base = info["public_base_url"].rstrip("/")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(root), text=True).strip()
    result = subprocess.run([
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", info["ssh_alias"],
        "cd /data/wengtengjin/tracememory-aml && set -a && . ./.env && set +a && python3 -c 'import os; print(os.environ[\"MEMORY_API_KEY\"])'",
    ], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise SystemExit("Cannot retrieve task-specific service credential; no draft written")
    key = result.stdout.strip()
    if len(key) < 24 or "\n" in key:
        raise SystemExit("Invalid credential response; no value disclosed")
    ingress_kind = info.get("ingress_kind", "temporary_quick_tunnel")
    ingress_note = (
        "Ingress uses an account-assigned ngrok development hostname, preserved across agent restarts. "
        "Agent runs on the participant server under a dedicated restart loop; capture/inspection is disabled. "
        "Free-plan quota is 1 GB outbound and 20,000 HTTP requests/month; account usage and Full-suite "
        "capacity are not certified. Server/container reboot recovery is manual at this stage. "
        "This application requests integration access/Smoke review, not a Full run. "
        if ingress_kind == "ngrok_dev_domain" else
        "IMPORTANT: current ingress is a temporary Cloudflare Quick Tunnel for integration preparation, "
        "with no fixed-hostname/reboot guarantee. It must be replaced or accepted explicitly before formal use. "
    )
    notes = (
        "TraceMemory protocol baseline for Multimodal integration. Public source: "
        + info["project_url"] + "; fixed commit: " + commit + ". "
        "Newly authored with AI assistance for wengtengjin/TraceMemory; no third-party memory code copied. "
        "Python 3.9+ standard library, SQLite WAL; synchronous Add commits before acknowledging, "
        "idempotent request_id, exact user_id isolation, ordered text/Base64 image evidence. "
        "Search uses user-local BM25 and exact image hashes, returns original evidence only. "
        "No LLM, Embedding or Reranker models are currently called; no unseen-image visual understanding. "
        "Please confirm whether this no-model baseline is eligible for academic Multimodal Smoke. "
        "Bearer authentication; /add, /search, /health; 48 MiB HTTP body limit, "
        "10 MiB decoded per image, 30 MiB decoded media per Add/Search response; top_k <= 100. "
        "At most 8 active requests, excess returns 429; initial requested Add/Search concurrency 1/1. "
        "Storage and full-suite capacity not yet certified. Local and deployed synthetic checks passed. "
        + ingress_note +
        "No official Smoke or Full has been run; this draft does not claim a score or Full readiness."
    )
    payload = {
        "leaderboard_track": "academic", "evaluation_route": "participant_api",
        "invitation_code": None, "contact_email": info["contact_email"],
        "contact_name": info["contact_name"], "organization": info["organization"],
        "system_name": info["system_name"], "version_name": info["version_name"],
        "add_endpoint": base + "/add", "search_endpoint": base + "/search",
        "add_status_endpoint": None, "health_endpoint": base + "/health",
        "api_auth_scheme": "bearer", "memory_api_key": key,
        "project_url": info["project_url"], "notes": notes,
    }
    assert len(notes) <= 4000
    write_private(root / "docs/评测申请.private.json", payload)
    print("Prepared docs/评测申请.private.json (permissions 0600). Credential not displayed.")
    print("Draft only: registration NOT submitted; official Smoke/Full NOT started.")
    if args.check_email:
        request = Request(
            "https://agentmemoryleaderboard.ai/evaluation-access-requests/check-email",
            data=json.dumps({"contact_email": info["contact_email"]}).encode(),
            headers={"Content-Type": "application/json",
                     "Origin": "https://agentmemoryleaderboard.ai",
                     "Referer": "https://agentmemoryleaderboard.ai/evaluation",
                     "User-Agent": "Mozilla/5.0"}, method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                value = json.load(response)
        except HTTPError as error:
            raise SystemExit("Official email precheck failed: HTTP %s; registration was not submitted" % error.code)
        available = value.get("available")
        if type(available) is not bool:
            raise SystemExit("Email-check response did not establish availability")
        write_private(root / "docs/邮箱检查.private.json", {"available": available})
        print("Official email availability check:", available)


if __name__ == "__main__":
    main()
