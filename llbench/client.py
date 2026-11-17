import json, time, threading, subprocess
import httpx, psutil
from .manage import base, process
from .common import ROOT, dump


class IncompleteResponseError(RuntimeError):
    """The connection ended before a complete API response arrived."""


class Client:
    def __init__(self, c):
        if c["benchmark"]["cache_policy"] != "cache_prompt_false":
            raise RuntimeError("Only verified cache_prompt_false policy is supported")
        if c["generation"]["reasoning_budget"] != -1:
            raise RuntimeError("Thinking MAX requires unrestricted reasoning budget -1")
        self.c = c
        self.http = httpx.Client(
            base_url=base(c), timeout=c["benchmark"]["request_timeout"], trust_env=False
        )

    def get(self, path):
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path, body):
        r = self.http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def tokens(self, text):
        return len(
            self.post("/tokenize", {"content": text, "add_special": False})["tokens"]
        )

    def body(self, prompt, mode, max_tokens):
        return {
            "model": self.c["model"]["repo"],
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": self.c["generation"]["temperature"],
            "seed": self.c["generation"]["seed"],
            "max_tokens": max_tokens,
            "top_k": self.c["generation"]["top_k"],
            "top_p": self.c["generation"]["top_p"],
            "min_p": self.c["generation"]["min_p"],
            "repeat_penalty": self.c["generation"]["repeat_penalty"],
            "cache_prompt": False,
            "timings_per_token": True,
            "chat_template_kwargs": {"enable_thinking": mode == "thinking"},
            "reasoning_budget_tokens": self.c["generation"]["reasoning_budget"],
            "reasoning_format": "deepseek",
            **({"reasoning_effort": "none"} if mode == "no-thinking" else {}),
        }

    def request(self, body):
        retries = self.c["benchmark"]["technical_retries"]
        failures = []
        for attempt in range(retries + 1):
            try:
                result = self._request(body)
                result["technical_attempts"] = attempt + 1
                result["technical_failures"] = failures
                return result
            except (
                httpx.TransportError,
                httpx.HTTPStatusError,
                IncompleteResponseError,
            ) as exc:
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code not in (408, 429)
                    and exc.response.status_code < 500
                ):
                    raise
                failure = {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                failures.append(failure)
                dump(
                    ROOT / "logs" / f"technical-failure-{time.time_ns()}.json",
                    {"request": body, **failure},
                )
                if attempt == retries:
                    raise
                print(
                    f"Technical failure, retry {attempt + 1}/{retries}: {exc}",
                    flush=True,
                )
                time.sleep(self.c["benchmark"]["retry_delay_seconds"])

    def _request(self, body):
        chunks = []
        events = []
        content = ""
        reasoning = ""
        usage = {}
        timings = {}
        finish = None
        first = None
        first_visible = None
        first_reason = None
        done = threading.Event()
        samples = []
        p = process()

        def monitor():
            while not done.is_set():
                s = {
                    "elapsed_s": time.perf_counter() - start,
                    "system_ram_used_bytes": psutil.virtual_memory().used,
                }
                if p:
                    try:
                        s.update(
                            server_rss_bytes=p.memory_info().rss,
                            server_cpu_percent=p.cpu_percent(),
                        )
                    except psutil.Error:
                        pass
                try:
                    r = subprocess.run(
                        [
                            "nvidia-smi",
                            "--query-gpu=memory.used,utilization.gpu",
                            "--format=csv,noheader,nounits",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if r.returncode == 0:
                        s["nvidia_device_samples_mib_percent"] = r.stdout.strip()
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
                samples.append(s)
                done.wait(self.c["benchmark"]["monitor_interval"])

        start = time.perf_counter()
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        try:
            with self.http.stream("POST", "/v1/chat/completions", json=body) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    now = (time.perf_counter() - start) * 1000
                    if not line:
                        continue
                    events.append({"at_ms": now, "line": line})
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        continue
                    try:
                        x = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise IncompleteResponseError("Malformed SSE data") from exc
                    chunks.append(x)
                    if "error" in x:
                        raise RuntimeError(x["error"])
                    usage = x.get("usage") or usage
                    timings = x.get("timings") or timings
                    for ch in x.get("choices", []):
                        d = ch.get("delta", {})
                        v = d.get("content") or ""
                        q = d.get("reasoning_content") or ""
                        if v or q:
                            if first is None:
                                first = now
                        if v and first_visible is None:
                            first_visible = now
                        if q and first_reason is None:
                            first_reason = now
                        content += v
                        reasoning += q
                        finish = ch.get("finish_reason") or finish
            total = (time.perf_counter() - start) * 1000
        except BaseException as exc:
            dump(
                ROOT / "logs" / f"request-error-{time.time_ns()}.json",
                {
                    "request": body,
                    "events": events,
                    "chunks": chunks,
                    "answer": content,
                    "reasoning": reasoning,
                    "timings": timings,
                    "usage": usage,
                    "error": str(exc),
                },
            )
            raise
        finally:
            done.set()
            thread.join(timeout=3)
        if not finish:
            dump(
                ROOT / "logs/incomplete-response.json",
                {
                    "request": body,
                    "events": events,
                    "chunks": chunks,
                    "timings": timings,
                    "usage": usage,
                },
            )
            raise IncompleteResponseError(
                "Incomplete SSE response: missing finish_reason"
            )
        cached = timings.get(
            "cache_n", usage.get("prompt_tokens_details", {}).get("cached_tokens")
        )
        if cached is None:
            dump(
                ROOT / "logs/missing-cache-metrics.json",
                {
                    "request": body,
                    "events": events,
                    "chunks": chunks,
                    "timings": timings,
                    "usage": usage,
                },
            )
            raise RuntimeError(
                "Cannot confirm cache policy: missing per-request cache count"
            )
        if cached != 0:
            dump(
                ROOT / "logs/cache-error.json",
                {"request": body, "timings": timings, "usage": usage, "events": events},
            )
            raise RuntimeError(
                f"Prompt cache reused {cached} tokens despite cache_prompt=false"
            )
        gpu_mib = []
        for sample in samples:
            for row in sample.get("nvidia_device_samples_mib_percent", "").splitlines():
                try:
                    gpu_mib.append(float(row.split(",")[0]))
                except ValueError:
                    pass
        n = timings.get("predicted_n", usage.get("completion_tokens"))
        details = usage.get("completion_tokens_details", {})
        metrics = {
            "ttft_ms": first,
            "ttft_visible_ms": first_visible,
            "ttft_reasoning_ms": first_reason,
            "prefill_ms": timings.get("prompt_ms"),
            "prefill_tps": timings.get("prompt_per_second"),
            "decode_ms": timings.get("predicted_ms"),
            "decode_tps": timings.get("predicted_per_second"),
            "total_ms": total,
            "input_tokens": usage.get("prompt_tokens", timings.get("prompt_n")),
            "output_tokens": n,
            "total_generated_tokens": n,
            "reasoning_tokens": details.get("reasoning_tokens"),
            "final_answer_tokens": details.get("content_tokens"),
            "cached_tokens": cached,
            "sampled_gpu_device_peak_used_mib": max(gpu_mib) if gpu_mib else None,
            "server_peak_rss_bytes": max(
                (s["server_rss_bytes"] for s in samples if "server_rss_bytes" in s),
                default=None,
            ),
        }
        return {
            "request": body,
            "answer": content,
            "reasoning": reasoning,
            "finish_reason": finish,
            "completed": finish == "stop",
            "metrics": metrics,
            "timings": timings,
            "usage": usage,
            "api_chunks": chunks,
            "sse_events": events,
            "resource_samples": samples,
            "measurement_notes": [
                "TTFT is arrival of first nonempty decoded text/reasoning delta, an observable upper bound on token TTFT; role/empty/usage chunks excluded. Special tokens and parser buffering are not observable.",
                "Final/reasoning token counts are null unless supplied by API; total generation includes reasoning and special tokens.",
                "RSS/CPU and NVIDIA memory are sampled, device-wide NVIDIA memory includes other processes; Metal GPU usage unavailable.",
                "Timing rates use per-request server timings only.",
            ],
        }
