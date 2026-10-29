"""AML Multimodal Smoke client. Default is read-only; Full is not implemented."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from .prepare_application import write_private
except ImportError:
    from prepare_application import write_private

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://agentmemoryleaderboard.ai"
RECEIPT = ROOT / "docs/官方Smoke.private.json"


def receipt_for_version(version_id, base=RECEIPT):
    if not version_id:return base
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',version_id):
        raise ValueError('invalid version identifier')
    if base.exists() and str(json.loads(base.read_text()).get('version_id'))!=version_id:
        return base.with_name('官方Smoke-'+version_id+'.private.json')
    return base


class Client:
    def __init__(self, key):
        self.key = key

    def api(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", "X-Leaderboard-Api-Key": self.key,
                   "Origin": ORIGIN, "Referer": ORIGIN + "/evaluation", "User-Agent": "Mozilla/5.0"}
        with urlopen(Request(ORIGIN + path, data=data, headers=headers), timeout=45) as response:
            return json.load(response)


def smoke_payload(version_id, contract=None):
    schema=(contract or {}).get('options',{})
    concurrency={}
    for name in ('max_add_concurrency','search_concurrency'):
        limits=schema.get(name,{})
        minimum=limits.get('minimum',16)
        maximum=limits.get('maximum',64 if name=='max_add_concurrency' else 256)
        if type(minimum) is not int or type(maximum) is not int or not 1<=minimum<=maximum:
            raise ValueError('official concurrency contract is invalid')
        concurrency[name]=minimum
    return {"version_id": version_id, "benchmark_type": "multimodal", "phase": "smoke",
            "options": dict(concurrency, top_k=100, media_transport="inline_data_uri"),
            "run_label": "TraceMemory-Multimodal-Smoke-" + str(version_id)}


def run(start=False, version_id=None):
    key_file = ROOT / "secrets/aml-key.txt"
    key = key_file.read_text().strip() if key_file.exists() else ""
    if not key or "PASTE_" in key:
        print("Waiting for organizer AML Key. No official request or evaluation started.")
        return
    client = Client(key)
    receipt_path=receipt_for_version(version_id)
    session = client.api("/access/validate", {"api_key": key})
    if session.get("valid") is not True or session.get("leaderboard_track") != "academic":
        raise SystemExit("Key is not verified for the academic board; no evaluation started")
    if session.get("system_name") != "TraceMemory" or session.get("endpoints_configured") is False:
        raise SystemExit("Key/system/endpoints mismatch; no evaluation started")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        identifier = receipt.get("evaluation_id")
        if identifier:
            job = client.api("/api/v1/evaluations/" + quote(str(identifier), safe=""))
            receipt.update(last_checked_at=datetime.now(timezone.utc).isoformat(), job=job)
            write_private(receipt_path, receipt)
            print(json.dumps({"evaluation_id": identifier, "status": job.get("status"),
                              "phase": job.get("phase"), "benchmark_type": job.get("benchmark_type")}))
            return
        raise SystemExit("A Smoke attempt already exists with uncertain state. Inspect privately; never submit blindly.")
    versions = client.api("/access/versions").get("versions", [])
    jobs = client.api("/api/v1/evaluations?limit=100&offset=0")
    write_private(ROOT / "docs/官方接入状态.private.json", {"session": session, "versions": versions, "jobs": jobs})
    print("Academic key verified; current version/job metadata saved privately.")
    if not start:
        print("Read-only check complete. No Smoke or Full started.")
        return
    info = json.loads((ROOT / "docs/私人报名信息.json").read_text())
    candidates = [v for v in versions if v.get("status") == "active" and
                  (str(v.get("id")) == version_id if version_id else v.get("version_name") == info["version_name"])]
    if len(candidates) != 1:
        raise SystemExit("Select one unambiguous active version before Smoke")
    version = candidates[0]
    identifier = str(version["id"])
    bound = client.api("/access/version-endpoints").get("versions", [])
    endpoints = [v for v in bound if str(v.get("id")) == identifier]
    base = info["public_base_url"].rstrip("/")
    if len(endpoints) != 1 or endpoints[0].get("add_endpoint") != base + "/add" or endpoints[0].get("search_endpoint") != base + "/search":
        raise SystemExit("Bound API endpoints differ from the verified deployment")
    if any(str(j.get("version_id")) == identifier and j.get("benchmark_type") == "multimodal" and j.get("phase") in ("smoke", "full")
           for j in jobs.get("evaluations", [])):
        raise SystemExit("A Smoke/Full exists for this version. Inspect the existing job before retrying.")
    if jobs.get("next_offset") is not None:
        raise SystemExit("Job history needs further pagination before safe duplicate checks")
    catalog = client.api("/api/v1/evaluation-tracks").get("tracks", [])
    track = next((t for t in catalog if t.get("id") == "multimodal"), {})
    contract = client.api("/api/v1/evaluation-tracks/multimodal/contract")
    eligibility = client.api("/api/v1/versions/" + quote(identifier, safe="") + "/track-eligibility")
    admission = eligibility.get("tracks", {}).get("multimodal", {})
    if not track.get("enabled") or not track.get("routes", {}).get("smoke", {}).get("worker_available"):
        raise SystemExit("Multimodal Smoke has no enabled healthy route")
    if admission.get("bound") is not True or admission.get("smoke_allowed") is not True:
        raise SystemExit("Current version has no confirmed Smoke admission; check contract/quota/binding")
    with urlopen(base + "/health", timeout=20) as response:
        if json.load(response).get("status") != "ok":
            raise SystemExit("Deployment is not healthy")
    receipt = {"state": "sending", "created_at": datetime.now(timezone.utc).isoformat(),
               "version_id": identifier, "contract": contract, "payload": smoke_payload(identifier,contract)}
    write_private(receipt_path, receipt)
    try:
        result = client.api("/api/v1/evaluations", receipt["payload"])
        receipt.update(state="submitted", response=result, evaluation_id=result.get("evaluation_id"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        receipt["state"] = "unknown"
        write_private(receipt_path, receipt)
        raise SystemExit("Smoke submission state unclear; private attempt saved, do not resubmit automatically")
    write_private(receipt_path, receipt)
    print("One Multimodal Smoke submitted. ID saved privately. No Full started.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="start one admitted, deduplicated Multimodal Smoke")
    parser.add_argument("--version-id")
    args = parser.parse_args()
    try:
        run(args.start, args.version_id)
    except HTTPError as error:
        raise SystemExit("Official API returned HTTP %s; response not printed to protect credentials" % error.code)
    except (URLError, TimeoutError, ValueError, KeyError):
        raise SystemExit("Official API not ready or schema changed; no automatic resubmission")


if __name__ == "__main__":
    main()
