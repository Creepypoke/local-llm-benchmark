#!/usr/bin/env python3
import argparse, json, datetime, traceback, shutil
from pathlib import Path
from llbench.common import (
    ROOT,
    config,
    dump,
    sha,
    run,
    hardware,
    binary,
    effective_server,
    source_hash,
)
from llbench.client import Client
from llbench.manage import process
from llbench.context import prepare
from llbench.validate import validate, semantic_validate
from llbench.report import render


def obtain_response(client, body, path):
    """Reuse a received response even if scoring/reporting was interrupted."""
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("request") != body or not raw.get("finish_reason"):
            raise RuntimeError(f"Incompatible or incomplete saved response: {path}")
        print(f"Reusing received response: {path.name}", flush=True)
        return raw
    raw = client.request(body)
    dump(path, raw)
    return raw


def probe(client, c, output):
    prompt = "Проверка API: вычисли 19 × 23 и кратко объясни вычисление."
    records = {}
    templates = {}
    for mode in ["no-thinking", "thinking"]:
        body = client.body(prompt, mode, c["generation"]["max_tokens"])
        templates[mode] = client.post("/apply-template", body)["prompt"]
        raw = client.request(body)
        dump(output / "raw" / f"probe-{mode}.json", raw)
        records[mode] = raw
    off = records["no-thinking"]
    on = records["thinking"]
    # Qwen templates: OFF explicitly closes empty thinking; ON opens the thinking prefix.
    off_suffix = templates["no-thinking"].rsplit("assistant", 1)[-1]
    on_suffix = templates["thinking"].rsplit("assistant", 1)[-1]
    distinct = templates["no-thinking"] != templates["thinking"]
    off_ok = (
        distinct
        and "</think>" in off_suffix
        and not off["reasoning"].strip()
        and "<think>" not in off["answer"]
        and bool(off["answer"].strip())
        and off["completed"]
    )
    on_ok = (
        distinct
        and "<think>" in on_suffix
        and "</think>" not in on_suffix
        and bool(on["reasoning"].strip())
        and bool(on["answer"].strip())
        and on["completed"]
    )
    result = {
        "off_confirmed": off_ok,
        "max_confirmed": on_ok,
        "templates": templates,
        "mechanism": "chat_template_kwargs.enable_thinking false/true; OFF also reasoning_effort=none; server and requests reasoning_budget_tokens=-1; MAX means unrestricted reasoning, not an unsupported effort label.",
    }
    dump(output / "raw/thinking-verification.json", result)
    if not off_ok or not on_ok:
        raise RuntimeError(
            "Thinking switching UNCONFIRMED; inspect raw probes and template evidence; benchmark not started"
        )
    return result


def main():
    p = argparse.ArgumentParser(
        description="Real streaming benchmark of an isolated llama.cpp instance"
    )
    p.add_argument("--all", action="store_true")
    p.add_argument("--task", type=int, choices=range(1, 10))
    p.add_argument("--mode", choices=["thinking", "no-thinking"])
    p.add_argument(
        "--resume",
        action="store_true",
        help="Continue without re-requesting obtained answers",
    )
    p.add_argument("--output", type=Path, default=ROOT / "reports")
    p.add_argument("--config", type=Path)
    p.add_argument("--probe-only", action="store_true")
    p.add_argument(
        "--no-judge",
        action="store_true",
        help="Leave semantic validation pending for manual review",
    )
    a = p.parse_args()
    c = config(a.config)
    try:
        retries = c["benchmark"]["technical_retries"]
        delay = c["benchmark"]["retry_delay_seconds"]
        if type(retries) is not int or retries < 0 or delay < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "technical_retries must be a nonnegative integer; retry_delay_seconds must be nonnegative"
        )
    if a.all and a.task:
        p.error("--all and --task are mutually exclusive")
    if c["server"]["parallel"] != 1:
        raise RuntimeError("Sequential benchmark requires parallel=1")
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    previous = (
        json.loads((out / "results.json").read_text(encoding="utf-8"))
        if (out / "results.json").exists()
        else None
    )
    if previous and not a.resume:
        raise RuntimeError(
            "Output already contains results. Use --resume to skip obtained answers or choose a new directory."
        )
    if not process():
        raise RuntimeError("Project-managed server is not running")
    client = Client(c)
    client.get("/health")
    props = client.get("/props")
    models = client.get("/v1/models")
    from llbench.install import verify

    installed_runtime = verify(c)
    commit = installed_runtime["commit"]
    if commit[:7] not in props.get("build_info", ""):
        raise RuntimeError("Running server binary differs from source commit")
    modelpath = ROOT / "models" / c["model"]["file"]
    modelhash = sha(modelpath)
    lock = json.loads((ROOT / "config/model-lock.json").read_text(encoding="utf-8"))
    if any(lock.get(k) != v for k, v in c["model"].items()):
        raise RuntimeError("Model metadata differs from download lock")
    if modelhash != lock["sha256"]:
        raise RuntimeError("Model SHA-256 differs from download lock")
    if Path(props["model_path"]).resolve() != modelpath.resolve():
        raise RuntimeError("Server loaded a different model path")
    if props["default_generation_settings"]["n_ctx"] != c["server"]["context_size"]:
        raise RuntimeError("Effective context differs from config")
    ids = [m["id"] for m in models["data"]]
    if c["model"]["repo"] not in ids:
        raise RuntimeError(f"Unexpected loaded model: {ids}")
    state = ROOT / "logs/server-config.json"
    if not state.exists():
        raise RuntimeError(
            "Managed server configuration missing; use scripts/start-server"
        )
    server_config = json.loads(state.read_text(encoding="utf-8"))
    if server_config["requested"] != c["server"]:
        raise RuntimeError("Server settings changed: stop and restart server")
    # /props records actual model/context settings; command and logs include backend/offload decisions.
    tasks = json.loads((ROOT / "benchmarks/tasks.json").read_text(encoding="utf-8"))
    selected = [t for t in tasks if not a.task or t["id"] == a.task]
    metadata = {
        "hardware": hardware(),
        "llama_commit": commit,
        "llama_runtime": installed_runtime,
        "benchmark_source_sha256": source_hash(),
        "llama_version": run(
            [binary(), "--version"], stderr=__import__("subprocess").STDOUT
        ),
        "model": c["model"]["repo"],
        "model_revision": c["model"]["revision"],
        "model_sha256": modelhash,
        "quantization": c["model"]["quantization"],
        "server": server_config,
        "props": props,
        "effective_server": effective_server(server_config),
        "models": models,
        "cache_policy": {
            "request_cache_prompt": False,
            "server_cache_ram": 0,
            "verify_cache_n_zero": True,
        },
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "measurement_limitations": [
            "TTFT is first nonempty SSE text arrival, upper bound on token TTFT; parser buffering applies.",
            "Semantic scoring uses same-model rubric judge; Attention technical correctness remains manual.",
            "GPU/CPU monitoring is sampled; no Metal GPU memory/utilization claim.",
        ],
    }
    data = {
        "schema_version": 1,
        "config": c,
        "metadata": metadata,
        "tasks": selected,
        "runs": [],
    }
    if previous:
        old = previous["metadata"]
        for key in ["model_sha256", "llama_commit", "quantization"]:
            if old[key] != metadata[key]:
                raise RuntimeError(f"Cannot resume: incompatible {key}")
        if (
            previous["config"]["generation"] != c["generation"]
            or previous["config"]["server"] != c["server"]
            or previous["tasks"] != selected
        ):
            raise RuntimeError("Cannot resume with changed inference settings or tasks")
        seen = set()
        for row in previous["runs"]:
            key = (row["task_id"], row["mode"])
            if key in seen:
                raise RuntimeError(
                    "Resume input has duplicate successful requests; archive and migrate it first"
                )
            seen.add(key)
        data["runs"] = previous["runs"]
        metadata["started_at"] = old["started_at"]
        metadata["resumed_at"] = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()
        metadata["previous_source_sha256"] = old.get("benchmark_source_sha256")
        metadata["thinking_verification"] = old.get("thinking_verification")
        metadata["reused_answers"] = len(data["runs"])
    metadata["measurement_policy"] = (
        "one_received_response_per_task_and_mode; retry_only_technical_failure"
    )
    metadata["status"] = "running"
    try:
        verification = metadata.get("thinking_verification") or {}
        if not (
            verification.get("off_confirmed") and verification.get("max_confirmed")
        ):
            metadata["thinking_verification"] = probe(client, c, out)
        print(
            "Thinking OFF and MAX confirmed; probes excluded from statistics.",
            flush=True,
        )
        if a.probe_only:
            dump(out / "probe-metadata.json", metadata)
            return
        longtext = prepare(client, c)
        metadata["long_context"] = json.loads(
            (ROOT / "benchmarks/long_context.json").read_text(encoding="utf-8")
        )
        metadata["tasks_sha256"] = sha(ROOT / "benchmarks/tasks.json")
        source_files = [
            ROOT / "benchmark.py",
            ROOT / "compare.py",
            ROOT / "requirements.txt",
            ROOT / "package.json",
            ROOT / "package-lock.json",
            *sorted((ROOT / "llbench").glob("*.py")),
            *sorted((ROOT / "scripts").glob("*.cjs")),
            *sorted((ROOT / "scripts").glob("*.sh")),
            *sorted((ROOT / "scripts").glob("*.ps1")),
            ROOT / "README.md",
            *sorted((ROOT / "config").glob("*.yaml")),
            ROOT / "config/model-lock.json",
            ROOT / "config/llama-release.json",
            ROOT / "config/runtime.json",
            ROOT / "benchmarks/tasks.json",
            ROOT / "benchmarks/long_context.txt",
            ROOT / "benchmarks/long_context.json",
        ]
        for source in source_files:
            destination = out / "raw/source" / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        dump(
            out / "raw/source/manifest.json",
            {str(source.relative_to(ROOT)): sha(source) for source in source_files},
        )
        metadata["validation_policy"] = (
            "manual_semantic" if a.no_judge else "local_model_rubric_judge"
        )
        for mode in [] if previous else ["no-thinking", "thinking"]:
            warm = client.request(
                client.body(
                    "Ответь одним словом: готов.", mode, c["generation"]["max_tokens"]
                )
            )
            dump(out / "raw" / f"warmup-{mode}.json", warm)
            if not warm["completed"]:
                raise RuntimeError("Warmup truncated")
        render(out, data)
        for task in selected:
            prompt = task["prompt"].replace("{long_context}", longtext)
            for mode in [a.mode] if a.mode else ["no-thinking", "thinking"]:
                if any(
                    row["task_id"] == task["id"] and row["mode"] == mode
                    for row in data["runs"]
                ):
                    print(
                        f"Already obtained: task-{task['id']}-{mode}; skipping",
                        flush=True,
                    )
                    continue
                rid = f"task-{task['id']}-{mode}-01"
                print(rid, flush=True)
                body = client.body(prompt, mode, task["max_tokens"])
                formatted = client.post("/apply-template", body)["prompt"]
                input_n = client.tokens(formatted)
                if input_n + body["max_tokens"] > c["server"]["context_size"]:
                    raise RuntimeError(
                        "Input and output budget exceed context; adjust explicit config"
                    )
                raw = obtain_response(client, body, out / "raw" / (rid + ".json"))
                v = validate(task, raw["answer"], c)
                if not a.no_judge and raw["completed"]:
                    v, judge = semantic_validate(
                        client,
                        task,
                        raw["answer"],
                        v,
                        c["generation"]["max_tokens"],
                    )
                    if judge:
                        dump(out / "raw" / (rid + "-judge.json"), judge)
                if not raw["completed"]:
                    v = {"status": "truncated", "passed": False, "detail": v}
                if mode == "no-thinking" and (
                    raw["reasoning"].strip() or "<think>" in raw["answer"]
                ):
                    v = {
                        "status": "thinking_off_unconfirmed",
                        "passed": False,
                        "detail": v,
                    }
                row = {
                    "id": rid,
                    "task_id": task["id"],
                    "task_name": task["name"],
                    "mode": mode,
                    "technical_attempts": raw.get("technical_attempts", 1),
                    "prompt": prompt,
                    "inference_source_sha256": metadata["benchmark_source_sha256"],
                    "answer": raw["answer"],
                    "reasoning": raw["reasoning"],
                    "finish_reason": raw["finish_reason"],
                    "completed": raw["completed"],
                    "metrics": raw["metrics"],
                    "validation": v,
                }
                data["runs"].append(row)
                dump(out / "raw" / (rid + "-validation.json"), v)
                render(out, data)
                print(
                    f"  {v['status']}; decode={raw['metrics']['decode_tps']}; generated={raw['metrics']['output_tokens']}; total={raw['metrics']['total_ms'] / 1000:.2f}s",
                    flush=True,
                )
        metadata["status"] = "complete"
    except BaseException as exc:
        metadata["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "error"
        )
        metadata["error"] = str(exc)
        dump(
            out / "failure.json",
            {"error": str(exc), "traceback": traceback.format_exc()},
        )
        raise
    finally:
        metadata["ended_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        log = ROOT / "logs/server.log"
        if log.exists():
            __import__("shutil").copy2(log, out / "raw/server.log")
        if not a.probe_only:
            render(out, data)


if __name__ == "__main__":
    main()
