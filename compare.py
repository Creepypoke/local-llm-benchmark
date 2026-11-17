#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from llbench.report import document, table, e, fmt, median, aggregate
from llbench.common import dump


def signature(d):
    c = d["config"]
    m = d["metadata"]
    return {
        "model_sha256": m["model_sha256"],
        "benchmark_source_sha256": m.get("benchmark_source_sha256"),
        "quantization": m["quantization"],
        "context_size": c["server"]["context_size"],
        "batch_size": c["server"]["batch_size"],
        "ubatch_size": c["server"]["ubatch_size"],
        "parallel": c["server"]["parallel"],
        "gpu_layers": c["server"]["gpu_layers"],
        "flash_attention": c["server"]["flash_attention"],
        "generation": c["generation"],
        "tasks_sha256": m.get("tasks_sha256"),
        "long_context": m.get("long_context"),
        "cache_policy": m["cache_policy"],
        "llama_commit": m["llama_commit"],
        "validation_policy": m.get("validation_policy"),
        "measurement_policy": m.get("measurement_policy"),
        "validation_config": c["validation"],
        "server_extra_args": c["server"]["extra_args"],
        "effective_server": {
            k: v
            for k, v in m.get("effective_server", {}).items()
            if k
            in [
                "gpu_layers_offloaded",
                "gpu_layers_total",
                "flash_attention",
                "context_size",
            ]
        },
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("inputs", nargs="+", type=Path)
    p.add_argument("--output", type=Path, default=Path("reports/comparison.html"))
    a = p.parse_args()
    datasets = [json.loads(x.read_text(encoding="utf-8")) for x in a.inputs]
    sigs = [signature(d) for d in datasets]
    diff = [k for k in sigs[0] if any(s.get(k) != sigs[0].get(k) for s in sigs[1:])]
    warning = (
        "Нестрого сопоставимые конфигурации: " + ", ".join(diff)
        if diff
        else "Конфигурации сопоставимы по проверенным параметрам; backend и оборудование различаются по назначению."
    )
    print(warning)
    body = (
        '<p class="warn">'
        + e(warning)
        + "</p><p>Относительные скорости и задержки вычислены относительно первого файла для той же задачи и режима. Для latency меньше лучше; для throughput больше лучше. Нет данных = —. Pending не приравнен к доказанной ошибке.</p>"
    )
    aggs = [d.get("aggregates") or aggregate(d["runs"]) for d in datasets]
    baseline = {(x["task_id"], x["mode"]): x for x in aggs[0]}
    rows = []
    keys = sorted(
        {
            (task["id"], mode)
            for data in datasets
            for task in data["tasks"]
            for mode in ["no-thinking", "thinking"]
        }
    )
    for path, d, aa in zip(a.inputs, datasets, aggs):
        label = d["metadata"]["hardware"]["hostname"] + " (" + str(path.parent) + ")"
        body += (
            "<details><summary>"
            + e(label)
            + "</summary><pre>"
            + e(
                json.dumps(
                    {"hardware": d["metadata"]["hardware"], "signature": signature(d)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            + "</pre></details>"
        )
        lookup = {(x["task_id"], x["mode"]): x for x in aa}
        for key in keys:
            x = lookup.get(key)
            b = baseline.get(key)
            row = [e(label), str(key[0]), key[1]]
            for metric in ["ttft_ms", "prefill_tps", "decode_tps", "total_ms"]:
                v = median(x, metric) if x else None
                bv = median(b, metric) if b else None
                row.append(fmt(v))
                row.append(
                    "—" if v is None or not bv else f"{(v / bv - 1) * 100:+.1f}%"
                )
            row.append(
                "—"
                if not x
                else f"{x['passed']}/{x['runs']} ({x['accuracy']:.0%}); pending={x['pending']}"
            )
            rows.append(row)
    body += table(
        [
            "Устройство",
            "Тест",
            "Thinking",
            "TTFT ms",
            "Δ baseline",
            "Prefill tok/s",
            "Δ baseline",
            "Decode tok/s",
            "Δ baseline",
            "Total ms",
            "Δ baseline",
            "Accuracy",
        ],
        rows,
    )
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(
        document("Сравнение Local LLM Benchmark", body), encoding="utf-8"
    )
    dump(
        a.output.with_suffix(".json"),
        {
            "inputs": [str(x) for x in a.inputs],
            "strictly_comparable": not diff,
            "different_fields": diff,
            "signatures": sigs,
        },
    )
    print(a.output)


if __name__ == "__main__":
    main()
