"""Synthetic quality/capacity diagnostics; never contacts AML or a model API."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import struct
import sys
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory_service import MemoryServer, MemoryStore


def image(color, level=6):
    # A generated one-pixel PNG fixture; changing compression keeps pixel identity.
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    binary = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    binary += chunk(b"IDAT", zlib.compress(b"\x00" + bytes(color), level)) + chunk(b"IEND", b"")
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(binary).decode()}}


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(len(values) * fraction + 0.999) - 1))]


def quality(directory):
    cases = [
        ("en_exact", "english_exact", "Alex recommended Lake Cafe after cycling.", "Lake Cafe", None),
        ("zh_exact", "chinese_exact", "小王在杭州骑车后推荐湖边咖啡馆。", "小王 湖边咖啡馆", None),
        ("synonym", "semantic_paraphrase", "Maya purchased an automobile in Kyoto.", "Who bought a car?", None),
        ("temporal", "latest_state", "2026-06: Sam moved to Berlin.", "Where does Sam live now?", None),
        ("cross_session", "cross_session_evidence", "Alex recommended jasmine tea.", "What drink did the cyclist recommend?", None),
        ("exact_image", "identical_image_bytes", "A red pixel supplied by Alex.", [image((255, 0, 0))], image((255, 0, 0))),
        ("reencoded_image", "same_pixels_different_encoding", "A red pixel supplied by Alex.", [image((255, 0, 0), 0)], image((255, 0, 0), 6)),
    ]
    rows = []
    for case_id, category, target, query, picture in cases:
        store = MemoryStore(directory / (case_id + ".sqlite3"))
        user = "synthetic:quality:" + case_id
        messages = [{"role": "user", "content": "Synthetic unrelated ledger item %d." % i} for i in range(180)]
        if case_id == "temporal":
            messages += [{"role": "user", "content": "2026-01: Sam lived in Paris. Sam lives in Paris.", "timestamp": 1767225600000}]
        store.add({"user_id": user, "request_id": "noise", "session_id": "history", "messages": messages})
        if case_id == "cross_session":
            store.add({"user_id": user, "request_id": "bridge", "session_id": "history-bridge",
                       "messages": [{"role": "user", "content": "The cyclist in last week's photo was Alex."}]})
        content = target if picture is None else [{"type": "text", "text": target}, picture]
        store.add({"user_id": user, "request_id": "target", "session_id": "latest",
                   "messages": [{"role": "user", "content": content, "timestamp": 1780272000000}]})
        expected = "mem_" + hashlib.sha256(json.dumps([user, "target", 0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result = store.search({"user_id": user, "query": query, "top_k": 100})["data"]
        rank = next((i + 1 for i, row in enumerate(result) if row["id"] == expected), None)
        bridge_rank = None
        if case_id == "cross_session":
            bridge_id = "mem_" + hashlib.sha256(json.dumps([user, "bridge", 0], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            bridge_rank = next((i + 1 for i, row in enumerate(result) if row["id"] == bridge_id), None)
        rows.append({"case": case_id, "category": category, "rank_within_100": rank,
                     "recall_at_1": int(rank == 1), "recall_at_5": int(rank is not None and rank <= 5),
                     "reciprocal_rank": 1 / rank if rank else 0,
                     "positive_score": bool(rank and result[rank - 1]["score"] > 0),
                     "bridge_evidence_rank": bridge_rank,
                     "all_required_evidence_at_5": bool(rank and rank <= 5 and bridge_rank and bridge_rank <= 5) if case_id == "cross_session" else None})
    # Synthetic deterministic fixtures diagnose behavior, not statistical generalization.
    return {"case_count": len(rows), "noise_records_per_case": 180,
            "recall_at_1": statistics.mean(row["recall_at_1"] for row in rows),
            "recall_at_5": statistics.mean(row["recall_at_5"] for row in rows),
            "mrr_at_100": statistics.mean(row["reciprocal_rank"] for row in rows), "cases": rows,
            "limitations": "Tiny synthetic diagnostic suite; includes constructed temporal confounder. Not an official benchmark or answer accuracy."}


def capacity(directory, sizes):
    measurements = []
    for count in sizes:
        path = directory / ("capacity-%d.sqlite3" % count)
        store = MemoryStore(path)
        key = "synthetic-capacity-key-not-a-production-credential"
        server = MemoryServer(("127.0.0.1", 0), store, key)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % server.server_port

        def call(endpoint, payload):
            request = Request(base + endpoint, data=json.dumps(payload).encode(),
                              headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
            with urlopen(request, timeout=90) as response:
                binary = response.read()
            return json.loads(binary), len(binary)

        try:
            user = "synthetic:capacity"
            started = time.perf_counter()
            for offset in range(0, count, 200):
                messages = [{"role": "user", "content": "TeamMember%d visited City%d, likes coffee and cycling; synthetic event reference %d." % (i, i % 53, i)}
                            for i in range(offset, min(count, offset + 200))]
                call("/add", {"user_id": user, "request_id": "batch-%d" % offset, "session_id": "session-%d" % offset, "messages": messages})
            add_seconds = time.perf_counter() - started

            def search(index):
                started = time.perf_counter()
                result, size = call("/search", {"user_id": user, "query": "TeamMember%d coffee City%d" % (index % count, index % 53), "top_k": 100})
                assert len(result["data"]) <= 100
                return (time.perf_counter() - started) * 1000, size

            serial = [search(i) for i in range(8)]
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=4) as pool:
                concurrent = list(pool.map(search, range(12)))
            concurrent_seconds = time.perf_counter() - started
            isolation, _ = call("/search", {"user_id": "synthetic:other", "query": "coffee", "top_k": 100})
            assert isolation == {"data": []}
            measurements.append({"records": count, "add_seconds": round(add_seconds, 4),
                                 "search_serial_p50_ms": round(statistics.median(t for t, _ in serial), 3),
                                 "search_serial_p95_ms": round(percentile([t for t, _ in serial], .95), 3),
                                 "search_concurrency4_p95_ms": round(percentile([t for t, _ in concurrent], .95), 3),
                                 "concurrency4_queries_per_second": round(12 / concurrent_seconds, 2),
                                 "response_bytes_mean": round(statistics.mean(size for _, size in serial)),
                                 "database_bytes": sum(p.stat().st_size for p in directory.glob(path.name + "*"))})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return measurements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000, 5000])
    args = parser.parse_args()
    if any(not 1 <= n <= 20000 for n in args.sizes):
        raise SystemExit("Diagnostic sizes must be between 1 and 20000")
    with tempfile.TemporaryDirectory(prefix="tracememory-diagnostics-") as directory:
        path = Path(directory)
        report = {"created_at": datetime.now(timezone.utc).isoformat(), "official_evaluation": False,
                  "models_called": False, "external_service_calls": False,
                  "quality": quality(path), "capacity": capacity(path, args.sizes),
                  "bandwidth_scenarios": []}
    for images, kib in ((1, 256), (10, 256), (20, 512)):
        encoded = images * ((kib * 1024 + 2) // 3) * 4
        report["bandwidth_scenarios"].append({"images_per_search": images, "decoded_kib_each": kib,
                                               "base64_response_bytes_lower_bound": encoded,
                                               "searches_per_1GB_upper_bound": int(1_000_000_000 // encoded)})
    report["bandwidth_note"] = "Estimates only; 1 GB modeled as 1,000,000,000 bytes. Excludes JSON, headers and other responses; actual allowance usage may differ. No realistic-size image load test was run."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
