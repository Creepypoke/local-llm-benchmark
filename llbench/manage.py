import argparse, os, sys, subprocess, time, socket, platform, shutil, json
from pathlib import Path
import httpx, psutil
from .common import ROOT, config, dump, sha, run, binary

STATE = ROOT / "logs/server-state.json"


def base(c):
    return f"http://{c['server']['host']}:{c['server']['port']}"


def process():
    if not STATE.exists():
        return None
    s = json.loads(STATE.read_text(encoding="utf-8"))
    try:
        p = psutil.Process(s["pid"])
        if abs(p.create_time() - s["create_time"]) < 0.01 and str(binary()) in " ".join(
            p.cmdline()
        ):
            return p
    except psutil.Error:
        pass
    return None


def stop():
    p = process()
    if p:
        if os.name == "nt":
            helper = (
                "import ctypes,time; k=ctypes.WinDLL('kernel32',use_last_error=True); assert k.AttachConsole("
                + str(p.pid)
                + "),ctypes.get_last_error(); k.SetConsoleCtrlHandler(None,True); assert k.GenerateConsoleCtrlEvent(0,0),ctypes.get_last_error(); time.sleep(0.5)"
            )
            result = subprocess.run(
                [sys.executable, "-c", helper],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                print(
                    "Console shutdown unavailable; terminating verified project process"
                )
                p.terminate()
        else:
            p.terminate()
        try:
            p.wait(timeout=30)
        except psutil.TimeoutExpired:
            p.kill()
            p.wait(timeout=10)
    STATE.unlink(missing_ok=True)


def start(c):
    if process():
        previous = json.loads(
            (ROOT / "logs/server-config.json").read_text(encoding="utf-8")
        )
        if previous["requested"] != c["server"]:
            raise RuntimeError(
                "Running server config differs. Stop server before restarting with changed settings."
            )
        httpx.get(base(c) + "/health", timeout=5, trust_env=False).raise_for_status()
        print("Project server is already healthy")
        return
    s = c["server"]
    if s["host"] != "127.0.0.1":
        raise RuntimeError("Only 127.0.0.1 is permitted")
    with socket.socket() as sock:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((s["host"], s["port"]))
        except OSError as e:
            raise RuntimeError(
                "Port occupied by another process; refusing to stop it"
            ) from e
    exe = binary()
    model = ROOT / "models" / c["model"]["file"]
    if not exe.exists() or not model.exists():
        raise RuntimeError("Run scripts/setup first")
    helptext = run([exe, "--help"], stderr=subprocess.STDOUT)
    required = [
        "--jinja",
        "--cache-ram",
        "--reasoning-budget",
        "--reasoning-format",
        "--perf",
    ]
    for flag in required:
        if flag not in helptext:
            raise RuntimeError(f"Incompatible llama.cpp: missing {flag}")
    args = [
        str(exe),
        "--model",
        str(model),
        "--alias",
        c["model"]["repo"],
        "--host",
        s["host"],
        "--port",
        str(s["port"]),
        "--ctx-size",
        str(s["context_size"]),
        "--gpu-layers",
        str(s["gpu_layers"]),
        "--batch-size",
        str(s["batch_size"]),
        "--ubatch-size",
        str(s["ubatch_size"]),
        "--parallel",
        str(s["parallel"]),
        "--jinja",
        "--reasoning-format",
        "deepseek",
        "--reasoning-budget",
        "-1",
        "--cache-ram",
        "0",
        "--cache-reuse",
        "0",
        "--perf",
        "--no-webui",
        "--verbosity",
        str(s["log_verbosity"]),
    ]
    adaptations = []
    if "--flash-attn" in helptext:
        args += ["--flash-attn", str(s["flash_attention"])]
    else:
        adaptations.append("Flash attention CLI unavailable; backend default used")
    protected = {
        "--host",
        "--port",
        "--model",
        "-m",
        "--alias",
        "--ctx-size",
        "-c",
        "--parallel",
        "-np",
        "--reasoning",
        "-rea",
        "--reasoning-budget",
        "--reasoning-format",
        "--chat-template",
        "--chat-template-file",
        "--chat-template-kwargs",
        "--jinja",
        "--no-jinja",
        "--cache-ram",
        "--cache-reuse",
        "--gpu-layers",
        "--n-gpu-layers",
        "-ngl",
        "--batch-size",
        "-b",
        "--ubatch-size",
        "-ub",
        "--flash-attn",
        "-fa",
    }
    if any(str(arg).split("=")[0] in protected for arg in s["extra_args"]):
        raise RuntimeError(
            "extra_args cannot override controlled model/network/template/cache parameters"
        )
    args += s["extra_args"]
    logpath = ROOT / "logs/server.log"
    log_start = logpath.stat().st_size if logpath.exists() else 0
    log = open(logpath, "ab", buffering=0)
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    p = subprocess.Popen(
        args,
        cwd=ROOT,
        stdout=log,
        stderr=log,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        startupinfo=startupinfo,
    )
    log.close()
    dump(STATE, {"pid": p.pid, "create_time": psutil.Process(p.pid).create_time()})
    dump(
        ROOT / "logs/server-config.json",
        {
            "command": args,
            "log_start_offset": log_start,
            "requested": s,
            "adaptations": adaptations,
            "backend_build": json.loads(
                (ROOT / "config/build.json").read_text(encoding="utf-8")
            )
            if (ROOT / "config/build.json").exists()
            else None,
        },
    )
    deadline = time.monotonic() + s["startup_timeout"]
    while time.monotonic() < deadline:
        if p.poll() is not None:
            raise RuntimeError("Server exited; see logs/server.log")
        try:
            if (
                httpx.get(base(c) + "/health", timeout=2, trust_env=False).status_code
                == 200
            ):
                print(base(c))
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    stop()
    raise RuntimeError("Server startup timeout; see logs/server.log")


def setup(c):
    if process():
        raise RuntimeError("Stop the project server before rebuilding")
    src = ROOT / "llama.cpp"
    commit = c["llama"]["commit"]
    if not src.exists():
        subprocess.run(
            ["git", "clone", "--no-checkout", c["llama"]["repository"], str(src)],
            check=True,
        )
    try:
        current = run(["git", "-C", src, "rev-parse", "HEAD"])
    except subprocess.CalledProcessError:
        current = ""
    if current != commit:
        subprocess.run(
            ["git", "-C", str(src), "fetch", "--depth", "1", "origin", commit],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(src), "checkout", "--detach", commit], check=True
        )
    if run(["git", "-C", src, "status", "--porcelain"]):
        raise RuntimeError("Local llama.cpp source modified; refusing to build")
    backend = c["server"]["backend"]
    if backend == "auto":
        backend = (
            "metal"
            if platform.system() == "Darwin" and platform.machine() == "arm64"
            else ("cuda" if shutil.which("nvcc") else "cpu")
        )
    if backend not in ("cpu", "cuda", "metal"):
        raise RuntimeError("backend must be auto, cpu, cuda or metal")
    if platform.system() == "Windows" and backend != "cuda":
        raise RuntimeError("Windows NVIDIA setup requires CUDA Toolkit / nvcc and MSVC")
    flags = [
        "-DGGML_METAL=" + ("ON" if backend == "metal" else "OFF"),
        "-DGGML_CUDA=" + ("ON" if backend == "cuda" else "OFF"),
    ]
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    subprocess.run(
        [
            "cmake",
            "-S",
            str(src),
            "-B",
            str(src / "build"),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DLLAMA_BUILD_TESTS=OFF",
            "-DLLAMA_CURL=OFF",
            *flags,
        ],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            "cmake",
            "--build",
            str(src / "build"),
            "--target",
            "llama-server",
            "-j",
            str(min(os.cpu_count() or 2, 8)),
        ],
        check=True,
        env=env,
    )
    dump(
        ROOT / "config/build.json",
        {
            "backend": backend,
            "commit": run(["git", "-C", src, "rev-parse", "HEAD"]),
            "flags": flags,
        },
    )
    os.environ["HF_HOME"] = str(ROOT / "models/.hf-cache")
    from huggingface_hub import hf_hub_download

    m = c["model"]
    path = hf_hub_download(
        m["repo"], m["file"], revision=m["revision"], local_dir=ROOT / "models"
    )
    dump(
        ROOT / "config/model-lock.json",
        dict(m, sha256=sha(path), size=Path(path).stat().st_size),
    )
    subprocess.run(
        [
            "npm" + (".cmd" if os.name == "nt" else ""),
            "ci",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ],
        cwd=ROOT,
        check=True,
    )
    print("Setup complete")


def main():
    for name in ["logs", "models", "reports"]:
        (ROOT / name).mkdir(exist_ok=True)
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["setup", "start", "stop"])
    p.add_argument("--config")
    a = p.parse_args()
    c = config(a.config)
    {"setup": lambda: setup(c), "start": lambda: start(c), "stop": stop}[a.action]()


if __name__ == "__main__":
    main()
