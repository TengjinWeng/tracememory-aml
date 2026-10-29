"""Submit one reviewed AML integration application. Never starts Smoke or Full."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from prepare_application import write_private

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://agentmemoryleaderboard.ai"


def post(path, payload):
    request = Request(ORIGIN + path, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "Origin": ORIGIN,
        "Referer": ORIGIN + "/evaluation", "User-Agent": "Mozilla/5.0",
    })
    with urlopen(request, timeout=45) as response:
        return response.status, json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submit", action="store_true", help="actually send the access request once")
    args = parser.parse_args()
    info = json.loads((ROOT / "docs/私人报名信息.json").read_text())
    packet = json.loads((ROOT / "docs/评测申请.private.json").read_text())
    receipt_path = ROOT / "docs/申请回执.private.json"
    if receipt_path.exists():
        raise SystemExit("A receipt/attempt already exists. Inspect it privately before any new request.")
    if info.get("ingress_kind") != "ngrok_dev_domain" or not info.get("integration_public_verified"):
        raise SystemExit("A verified fixed integration endpoint is required")
    base = info["public_base_url"].rstrip("/")
    for name, suffix in (("add_endpoint", "/add"), ("search_endpoint", "/search"), ("health_endpoint", "/health")):
        if packet.get(name) != base + suffix:
            raise SystemExit("Private application has stale endpoints; regenerate it first")
    if packet.get("contact_email") != info["contact_email"] or len(packet.get("notes", "")) > 4000:
        raise SystemExit("Private application validation failed")
    if not args.submit:
        print("Application preflight passed. No request submitted. Use --submit to send once.")
        return
    status, availability = post("/evaluation-access-requests/check-email", {"contact_email": packet["contact_email"]})
    if status != 200 or availability.get("available") is not True:
        raise SystemExit("Email has a key or pending application, or availability is unknown. No duplicate submitted.")
    receipt = {"attempted_at": datetime.now(timezone.utc).isoformat(), "state": "sending",
               "payload_sha256": hashlib.sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest(),
               "base_url": base, "smoke_started": False, "full_started": False}
    write_private(receipt_path, receipt)
    try:
        status, body = post("/evaluation-access-requests", packet)
        receipt.update(state="submitted", http_status=status, response=body)
        info["registration_submitted"] = True
        write_private(ROOT / "docs/私人报名信息.json", info)
    except HTTPError as error:
        receipt.update(state="rejected_http", http_status=error.code,
                       response_text=error.read(16000).decode("utf-8", errors="replace"))
        write_private(receipt_path, receipt)
        raise SystemExit("Application returned HTTP %s. Private response saved; no automatic retry." % error.code)
    except (URLError, TimeoutError, ValueError):
        receipt["state"] = "unknown"
        write_private(receipt_path, receipt)
        raise SystemExit("Application status unknown. Check mailbox/receipt; do not resubmit blindly.")
    write_private(receipt_path, receipt)
    print("Official integration application submitted. Receipt saved privately (0600).")
    print("Awaiting organizer review/email. No official Smoke or Full was started.")


if __name__ == "__main__":
    main()
