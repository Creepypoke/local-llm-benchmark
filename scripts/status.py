"""Print checkpoint progress without touching a running experiment."""

import json
from pathlib import Path

p = Path(__file__).resolve().parents[1] / "reports/results.json"
if not p.exists():
    print("No measured requests yet; inspect logs/full-benchmark.log")
else:
    d = json.loads(p.read_text(encoding="utf-8"))
    print("Status:", d["metadata"].get("status", "running"))
    print("Measured requests:", len(d["runs"]))
    for a in d["aggregates"]:
        print(
            a["task_id"],
            a["mode"],
            f"n={a['runs']} pass={a['passed']} fail={a['failed']} pending={a['pending']}",
        )
