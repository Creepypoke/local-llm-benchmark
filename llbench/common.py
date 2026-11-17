from pathlib import Path
import hashlib, json, platform, subprocess
import yaml, psutil

ROOT = Path(__file__).resolve().parents[1]


def config(path=None):
    return yaml.safe_load(
        Path(path or ROOT / "config/benchmark.yaml").read_text(encoding="utf-8")
    )


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def run(args, **kw):
    return subprocess.check_output([str(a) for a in args], text=True, **kw).strip()


def binary():
    metadata = ROOT / "config/runtime.json"
    if not metadata.exists():
        raise RuntimeError(
            "Run scripts/setup first to install the official llama.cpp binary"
        )
    info = json.loads(metadata.read_text(encoding="utf-8"))
    executable = (ROOT / info["executable"]).resolve()
    if not executable.is_relative_to((ROOT / "runtime").resolve()):
        raise RuntimeError(
            "Runtime executable must be inside the project runtime directory"
        )
    return executable


def hardware():
    cpu = platform.processor()
    gpu = None
    if platform.system() == "Linux":
        info = Path("/proc/cpuinfo")
        if info.exists():
            cpu = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in info.read_text(encoding="utf-8").splitlines()
                    if line.startswith("model name")
                ),
                cpu,
            )
    if platform.system() == "Windows":
        try:
            cpu = run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name) -join '; '",
                ]
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    if platform.system() == "Darwin":
        cpu = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        gpu = {
            "name": cpu + " integrated GPU",
            "memory_kind": "unified",
            "dedicated_vram_bytes": None,
        }
    try:
        rows = run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ]
        )
        gpu = {
            "nvidia_smi": rows,
            "memory_units": "MiB",
            "columns": ["name", "memory.total", "memory.free", "driver_version"],
        }
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    if gpu is None and platform.system() == "Linux":
        try:
            gpu = {"lspci": run(["lspci"])}
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    return {
        "cpu": cpu,
        "gpu": gpu,
        "ram_bytes": psutil.virtual_memory().total,
        "os": platform.platform(),
        "hostname": platform.node(),
    }


def effective_server(server_config):
    import re

    log = ROOT / "logs/server.log"
    if not log.exists():
        return {"source": "unavailable"}
    with log.open("rb") as f:
        f.seek(server_config.get("log_start_offset", 0))
        text = f.read().decode("utf-8", errors="replace")
    off = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", text)
    flash = re.findall(r"flash_attn\s*=\s*(\w+)", text)
    context = re.findall(r"n_ctx\s*=\s*(\d+)", text)
    return {
        "source": "startup server log",
        "gpu_layers_offloaded": int(off[-1][0]) if off else None,
        "gpu_layers_total": int(off[-1][1]) if off else None,
        "flash_attention": flash[-1] if flash else None,
        "context_size": int(context[-1]) if context else None,
        "backend_evidence": [
            line
            for line in text.splitlines()
            if any(
                s in line
                for s in [
                    "using device",
                    "offloaded ",
                    "flash_attn",
                    "n_batch ",
                    "n_ubatch ",
                    "n_threads =",
                ]
            )
        ][:30],
    }


def source_hash():
    files = [
        ROOT / "benchmark.py",
        ROOT / "requirements.txt",
        *sorted((ROOT / "llbench").glob("*.py")),
        *sorted((ROOT / "scripts").glob("*.cjs")),
    ]
    return hashlib.sha256(
        "\n".join(
            str(path.relative_to(ROOT)) + ":" + sha(path) for path in files
        ).encode()
    ).hexdigest()
