from datetime import datetime, timedelta
from .common import ROOT, dump, sha


def prepare(client, c):
    path = ROOT / "benchmarks/long_context.txt"
    meta = ROOT / "benchmarks/long_context.json"
    target = c["benchmark"]["long_context_tokens"]
    tol = c["benchmark"]["long_context_tolerance"]
    if path.exists():
        import json

        previous = json.loads(meta.read_text(encoding="utf-8"))
        if previous["sha256"] != sha(path):
            raise RuntimeError("Shared long context hash mismatch")
        if previous["model_revision"] != c["model"]["revision"]:
            raise RuntimeError("Shared long context tokenizer revision mismatch")
        n = client.tokens(path.read_text(encoding="utf-8"))
        if abs(n - target) > tol:
            raise RuntimeError(
                f"Existing context has {n} tokens, target {target}; do not silently regenerate shared input"
            )
        return path.read_text(encoding="utf-8")
    lines = []
    for i in range(1, 1001):
        level = "ERROR" if i in (931, 987) else ["INFO", "DEBUG", "WARN"][i % 3]
        stamp = (datetime(2026, 1, 1) + timedelta(seconds=i)).isoformat()
        lines.append(
            f"{stamp} {level} LOG-{i:05d} event=heartbeat service=worker status=processed"
        )
    # Tokenize every candidate with the loaded model, reduce verbose fields if needed.
    text = "\n".join(lines)
    if client.tokens(text) > target:
        lines = [
            f"+{i:04d}s {'ERROR' if i in (931, 987) else ['INFO', 'DEBUG', 'WARN'][i % 3]} LOG-{i:05d}"
            for i in range(1, 1001)
        ]
    text = "\n".join(lines)
    if client.tokens(text) > target + tol:
        raise RuntimeError("Base 1000 log lines exceed configured token target")
    # Deterministic payload extension near the beginning; first ERROR stays at 93% of lines.
    low = 0
    high = target
    while low < high:
        mid = (low + high + 1) // 2
        candidate = lines.copy()
        for i in range(1000):
            candidate[i] += " ok" * (mid // 1000 + (i < mid % 1000))
        n = client.tokens("\n".join(candidate))
        if n <= target:
            low = mid
        else:
            high = mid - 1
    for i in range(1000):
        lines[i] += " ok" * (low // 1000 + (i < low % 1000))
    text = "\n".join(lines)
    n = client.tokens(text)
    if abs(n - target) > tol:
        raise RuntimeError(f"Token target failed: {n}")
    path.write_text(text, encoding="utf-8")
    dump(
        meta,
        {
            "tokens": n,
            "sha256": sha(path),
            "first_error": "LOG-00931",
            "lines": 1000,
            "timestamp_origin": "2026-01-01T00:00:00Z",
            "model_revision": c["model"]["revision"],
        },
    )
    return text
