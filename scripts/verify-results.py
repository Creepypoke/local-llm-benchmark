"""Audit real result artifacts; never generate or substitute measurements."""

import argparse
import csv
import html
import json
from html.parser import HTMLParser
from pathlib import Path


class HTMLAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.charts = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "svg":
            self.charts += 1
        if tag == "a" and "href" in attrs:
            self.links.append(attrs["href"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--expected-runs", type=int)
    args = parser.parse_args()
    root = args.directory
    for name in ["results.json", "results.csv", "report.md", "report.html"]:
        assert (root / name).is_file(), f"Missing {name}"
    data = json.loads((root / "results.json").read_text(encoding="utf-8"))
    assert data["metadata"]["status"] == "complete", data["metadata"].get("status")
    runs = data["runs"]
    if args.expected_runs is not None:
        assert len(runs) == args.expected_runs, (len(runs), args.expected_runs)
    assert len(set(r["id"] for r in runs)) == len(runs)
    assert all(not r["id"].startswith(("warmup", "probe")) for r in runs)
    page = (root / "report.html").read_text(encoding="utf-8")
    markdown = (root / "report.md").read_text(encoding="utf-8")
    audit = HTMLAudit()
    audit.feed(page)
    assert audit.charts == 6, audit.charts
    for link in audit.links:
        if not link.startswith("#"):
            assert (root / link).is_file(), link
    with (root / "results.csv").open(encoding="utf-8-sig", newline="") as f:
        assert len(list(csv.DictReader(f))) == len(runs)
    for row in runs:
        raw = json.loads(
            (root / "raw" / (row["id"] + ".json")).read_text(encoding="utf-8")
        )
        timing = raw["timings"]
        assert timing["cache_n"] == 0
        assert raw["request"]["cache_prompt"] is False
        assert raw["request"]["chat_template_kwargs"]["enable_thinking"] == (
            row["mode"] == "thinking"
        )
        assert (
            raw["request"]["temperature"] == data["config"]["generation"]["temperature"]
        )
        assert raw["request"]["seed"] == data["config"]["generation"]["seed"]
        for actual, source in [
            ("prefill_ms", "prompt_ms"),
            ("prefill_tps", "prompt_per_second"),
            ("decode_ms", "predicted_ms"),
            ("decode_tps", "predicted_per_second"),
            ("output_tokens", "predicted_n"),
        ]:
            assert row["metrics"][actual] == timing[source], (row["id"], actual)
        assert row["metrics"]["total_ms"] > 0
        text_events = [
            event for event in raw["sse_events"] if event["line"].startswith("data:")
        ]
        assert text_events
        assert any(chunk.get("timings") == timing for chunk in raw["api_chunks"])
        for key in ["answer", "reasoning"]:
            assert row[key] == raw[key]
            assert html.escape(raw[key]) in page
            assert raw[key] in markdown
        assert row["completed"] == (row["finish_reason"] == "stop")
    print(
        f"PASS: {len(runs)} real requests, raw per-request timings, cache policy, CSV, complete texts, HTML links and 6 charts audited."
    )


if __name__ == "__main__":
    main()
