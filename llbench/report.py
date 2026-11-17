import csv, html, json, statistics
from collections import defaultdict
from .common import dump

METRICS = [
    "ttft_ms",
    "ttft_visible_ms",
    "ttft_reasoning_ms",
    "prefill_ms",
    "prefill_tps",
    "decode_ms",
    "decode_tps",
    "total_ms",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "final_answer_tokens",
    "total_generated_tokens",
    "server_peak_rss_bytes",
    "sampled_gpu_device_peak_used_mib",
]


def aggregate(runs, p95_min=20):
    groups = defaultdict(list)
    for r in runs:
        groups[(r["task_id"], r["mode"])].append(r)
    out = []
    for (task, mode), rows in sorted(groups.items()):
        a = {
            "task_id": task,
            "task_name": rows[0]["task_name"],
            "mode": mode,
            "runs": len(rows),
            "passed": sum(r["validation"]["passed"] is True for r in rows),
            "failed": sum(r["validation"]["passed"] is False for r in rows),
            "pending": sum(r["validation"]["passed"] is None for r in rows),
        }
        a["accuracy"] = a["passed"] / len(rows)
        a["metrics"] = {}
        for key in METRICS:
            vals = [
                r["metrics"][key] for r in rows if r["metrics"].get(key) is not None
            ]
            a["metrics"][key] = (
                None
                if not vals
                else {
                    "n": len(vals),
                    "median": statistics.median(vals),
                    "mean": statistics.mean(vals),
                    "min": min(vals),
                    "max": max(vals),
                    "stddev": statistics.stdev(vals) if len(vals) > 1 else None,
                    "p95": statistics.quantiles(vals, n=100, method="inclusive")[94]
                    if len(vals) > 1
                    else None,
                    "p95_reliability": "not_applicable"
                    if len(vals) < 2
                    else ("adequate" if len(vals) >= p95_min else "low"),
                }
            )
        out.append(a)
    return out


def median(a, key):
    return (a["metrics"].get(key) or {}).get("median")


def fmt(v):
    return "—" if v is None else f"{v:.2f}" if isinstance(v, (float, int)) else str(v)


def e(v):
    return html.escape(str(v))


def table(headers, rows):
    return (
        "<table><thead><tr>"
        + "".join("<th>" + e(h) + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + str(v) + "</td>" for v in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table>"
    )


def chart(aggs, key, title):
    vals = [median(a, key) for a in aggs]
    maximum = max((v for v in vals if v is not None), default=1) or 1
    height = 40 + len(aggs) * 26
    svg = f'<svg viewBox="0 0 900 {height}" role="img" aria-label="{e(title)}">'
    for i, (a, v) in enumerate(zip(aggs, vals)):
        y = 20 + i * 26
        w = 0 if v is None else v / maximum * 550
        color = "#32816c" if a["mode"] == "no-thinking" else "#9661b8"
        svg += f'<text x="0" y="{y + 14}" font-size="12">{e(a["task_name"])} / {a["mode"]}</text><rect x="255" y="{y}" width="{w}" height="18" fill="{color}"/><text x="{265 + w}" y="{y + 14}" font-size="12">{fmt(v)}</text>'
    return f"<section><h2>{e(title)}</h2>" + svg + "</svg></section>"


def document(title, body):
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + e(title)
        + "</title><style>body{font:15px system-ui;background:#f5f6f8;color:#19212c;max-width:1500px;margin:32px auto;padding:0 24px}h1{font-size:32px}section,.card{background:white;border:1px solid #dce1e6;border-radius:12px;padding:24px;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}th{background:#edf0f4}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px ui-monospace,monospace}details{max-width:700px}summary{cursor:pointer;color:#275c91}svg{width:100%}.warn{color:#8b4020}a{color:#275c91}</style><body><h1>"
        + e(title)
        + "</h1>"
        + body
        + "</body></html>"
    )


def render(output, data):
    output.mkdir(parents=True, exist_ok=True)
    runs = data["runs"]
    aggs = aggregate(runs, data["config"]["benchmark"]["p95_min_samples"])
    data["aggregates"] = aggs
    single = all(group["runs"] <= 1 for group in aggs)
    measure_label = "одно измерение на режим" if single else "медианы архивных запусков"
    dump(output / "results.json", data)
    with (output / "results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        fields = [
            "task_id",
            "task_name",
            "mode",
            "technical_attempts",
            "finish_reason",
            "completed",
            "validation_status",
            *METRICS,
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in runs:
            w.writerow(
                {
                    **{k: r.get(k) for k in fields if k not in METRICS},
                    "validation_status": r["validation"]["status"],
                    **{k: r["metrics"].get(k) for k in METRICS},
                }
            )
    hardware = data["metadata"]
    card_summary = {
        "CPU": hardware["hardware"]["cpu"],
        "GPU": hardware["hardware"]["gpu"],
        "RAM_bytes": hardware["hardware"]["ram_bytes"],
        "OS": hardware["hardware"]["os"],
        "llama_commit": hardware["llama_commit"],
        "Model": hardware["model"],
        "Quantization": hardware["quantization"],
        "Context_size": data["config"]["server"]["context_size"],
        "GPU_offload_requested": data["config"]["server"]["gpu_layers"],
        "Effective_server": hardware.get("effective_server"),
        "Backend": hardware["server"].get(
            "runtime", hardware["server"].get("backend_build")
        ),
        "status": hardware.get("status", "running"),
        "started_at": hardware["started_at"],
    }
    card = (
        '<div class="card"><h2>Оборудование и конфигурация</h2><pre>'
        + e(json.dumps(card_summary, ensure_ascii=False, indent=2))
        + "</pre><details><summary>Полные метаданные и фактические props сервера</summary><pre>"
        + e(json.dumps(hardware, ensure_ascii=False, indent=2))
        + "</pre></details></div>"
    )
    body = (
        card
        + "<p>Зелёный: OFF. Фиолетовый: MAX (unrestricted budget; output/context limit still applies). Каждая задача выполняется один раз в каждом режиме; повтор возможен только при технической ошибке до получения полного ответа. Для одного измерения разброс и P95 не оцениваются. «—» означает отсутствие измерения. Accuracy: подтверждённые pass / полученные ответы; pending не считается ошибкой и показан отдельно. Semantic judge — оценка той же моделью, требует аудита. P95 при n&lt;20 малонадёжен.</p>"
    )
    body += '<p class="warn">TTFT: время прихода первого непустого delta с текстом, верхняя наблюдаемая граница токенного TTFT. Парсер reasoning может буферизовать текст. Прогрев, probe и judge исключены. Кеш: cache_prompt=false, cache-ram=0; cache_n=0 проверяется у каждого запроса. Токены reasoning/final — только API, без выдуманных оценок.</p>'
    rows = []
    md = [
        "# Local LLM Benchmark",
        "",
        "```json",
        json.dumps(hardware, ensure_ascii=False, indent=2),
        "```",
        "",
        "Все показатели получены из реальных запросов; null/— = недоступно. TTFT — первый непустой delta, возможна буферизация. При n=1 standard deviation и P95 недоступны. Semantic judge — предварительная оценка той же моделью.",
        "",
        "| Тест | Оригинальная задача | Эталон | Без thinking | Thinking MAX |",
        "|---|---|---|---|---|",
    ]
    for t in data["tasks"]:
        prompt = next(
            (
                row["prompt"]
                for row in runs
                if row["task_id"] == t["id"] and row.get("prompt")
            ),
            t["prompt"],
        )
        if "{long_context}" in prompt:
            context_file = output / "raw/source/benchmarks/long_context.txt"
            if not context_file.exists():
                from .common import ROOT

                context_file = ROOT / "benchmarks/long_context.txt"
            if context_file.exists():
                prompt = prompt.replace(
                    "{long_context}", context_file.read_text(encoding="utf-8")
                )
        prompt_cell = (
            "<details><summary>Исходная задача (полностью)</summary><pre>"
            + e(prompt)
            + "</pre></details>"
        )
        markdown_prompt_cell = prompt_cell.replace("\n", "&#10;").replace("|", "&#124;")
        row = [
            e(t["name"]),
            prompt_cell,
            "<details><summary>Эталон</summary><pre>"
            + e(t["reference"])
            + "</pre></details>",
        ]
        mrow = [
            t["name"],
            markdown_prompt_cell,
            str(t["reference"]).replace("|", "\\|").replace("\n", " "),
        ]
        for mode in ["no-thinking", "thinking"]:
            rr = [r for r in runs if r["task_id"] == t["id"] and r["mode"] == mode]
            if not rr:
                row.append("Не запускалось")
                mrow.append("Не запускалось")
                continue
            # Representative nearest median total latency, not cherry-picked successful answer.
            med = statistics.median(r["metrics"]["total_ms"] for r in rr)
            rep = min(rr, key=lambda r: abs(r["metrics"]["total_ms"] - med))
            a = next(a for a in aggs if a["task_id"] == t["id"] and a["mode"] == mode)
            status = f"{rep['validation']['status']}; pass {a['passed']}/{a['runs']}, pending {a['pending']}; distinct answers {len(set(r['answer'] for r in rr))}; finish={rep['finish_reason']}"
            answer_caption = (
                "Ответ" if len(rr) == 1 else "Ответ репрезентативного запуска"
            )
            row.append(
                e(status)
                + f"<details><summary>{answer_caption}</summary><pre>"
                + e(rep["answer"])
                + "</pre></details><details><summary>Reasoning</summary><pre>"
                + e(rep["reasoning"])
                + "</pre></details>"
            )
            mrow.append(status + " [Полный ответ](#run-" + rep["id"] + ")")
        rows.append(row)
        md.append("| " + " | ".join(mrow) + " |")
    body += (
        "<section><h2>Ответы и правильность</h2>"
        + table(
            ["Тест", "Оригинальная задача", "Эталон", "Без thinking", "Thinking MAX"],
            rows,
        )
        + "</section>"
    )
    perf = []
    md += [
        "",
        f"## Производительность ({measure_label})",
        "",
        "| Тест | Thinking | TTFT ms | Prefill tok/s | Decode tok/s | Total s | Output tokens | Accuracy / Pending |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in aggs:
        row = [
            a["task_name"],
            a["mode"],
            fmt(median(a, "ttft_ms")),
            fmt(median(a, "prefill_tps")),
            fmt(median(a, "decode_tps")),
            fmt(
                median(a, "total_ms") / 1000
                if median(a, "total_ms") is not None
                else None
            ),
            fmt(median(a, "output_tokens")),
            f"{a['passed']}/{a['runs']}; pending {a['pending']}",
        ]
        perf.append([e(x) for x in row])
        md.append("| " + " | ".join(row) + " |")
    body += (
        f"<section><h2>Производительность ({measure_label})</h2>"
        + table(
            [
                "Тест",
                "Thinking",
                "TTFT ms",
                "Prefill tok/s",
                "Decode tok/s",
                "Total s",
                "Output tokens",
                "Accuracy / Pending",
            ],
            perf,
        )
        + "</section>"
    )
    changes = []
    for t in data["tasks"]:
        pair = {a["mode"]: a for a in aggs if a["task_id"] == t["id"]}
        if len(pair) != 2:
            continue
        row = [t["name"]]
        for k in ["ttft_ms", "total_ms", "decode_tps", "output_tokens"]:
            x = median(pair["no-thinking"], k)
            y = median(pair["thinking"], k)
            row.append(
                "—"
                if x is None or y is None or x == 0
                else f"{(y / x - 1) * 100:+.1f}%"
            )
        row.append(
            f"{pair['no-thinking']['accuracy']:.0%} (pending {pair['no-thinking']['pending']}) → {pair['thinking']['accuracy']:.0%} (pending {pair['thinking']['pending']})"
        )
        changes.append(row)
    body += (
        "<section><h2>Изменение MAX относительно OFF</h2>"
        + table(
            [
                "Тест",
                "TTFT",
                "Total latency",
                "Decode speed",
                "Generated tokens",
                "Quality (pass/all)",
            ],
            [[e(x) for x in r] for r in changes],
        )
        + "</section>"
    )
    md += [
        "",
        "## MAX относительно OFF",
        "",
        "| Тест | TTFT | Total | Decode | Tokens | Quality |",
        "|---|---|---|---|---|---|---|",
    ] + ["| " + " | ".join(r) + " |" for r in changes]
    for key, title in [
        ("decode_tps", "Decode throughput, tok/s"),
        ("ttft_ms", "TTFT, ms"),
        ("total_ms", "Total latency, ms"),
        ("output_tokens", "Generated tokens"),
        ("prefill_tps", "Prefill throughput, tok/s"),
        ("ttft_visible_ms", "OFF vs MAX: TTFT до видимого ответа, ms"),
    ]:
        body += chart(aggs, key, title)
    alltexts = ""
    for r in runs:
        alltexts += (
            '<details id="run-'
            + e(r["id"])
            + '"><summary>'
            + e(r["id"] + " — " + r["validation"]["status"])
            + "</summary><h3>Final answer</h3><pre>"
            + e(r["answer"])
            + "</pre><h3>Reasoning</h3><pre>"
            + e(r["reasoning"])
            + "</pre><h3>Validation</h3><pre>"
            + e(json.dumps(r["validation"], ensure_ascii=False, indent=2))
            + '</pre><a href="raw/'
            + e(r["id"])
            + '.json">Raw request / SSE / timings</a></details>'
        )
        md += [
            "",
            f'<a id="run-{r["id"]}"></a>',
            f"## {r['id']} — {r['validation']['status']}",
            "",
            "<details><summary>Final answer</summary>",
            "",
            r["answer"],
            "",
            "</details>",
            "",
            "<details><summary>Reasoning</summary>",
            "",
            r["reasoning"],
            "",
            "</details>",
            "",
            f"[Raw](raw/{r['id']}.json)",
        ]
    md += [
        "",
        "## Статистика",
        "",
        "```json",
        json.dumps(aggs, ensure_ascii=False, indent=2),
        "```",
    ]
    body += (
        "<section><h2>Все ответы без сокращений</h2>"
        + alltexts
        + "</section><section><h2>Полная статистика</h2><pre>"
        + e(json.dumps(aggs, ensure_ascii=False, indent=2))
        + "</pre></section>"
    )
    (output / "report.html").write_text(
        document("Local LLM Benchmark", body), encoding="utf-8"
    )
    (output / "report.md").write_text("\n".join(md), encoding="utf-8")
