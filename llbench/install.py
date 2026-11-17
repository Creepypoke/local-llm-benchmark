"""Install checksum-pinned official llama.cpp binaries; never compile sources."""

import json
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

import httpx

from .common import ROOT, dump, run, sha


def manifest(c):
    data = json.loads((ROOT / "config/llama-release.json").read_text(encoding="utf-8"))
    if (
        data["stable_release"] != c["llama"]["release"]
        or data["commit"] != c["llama"]["commit"]
    ):
        raise RuntimeError(
            "Release manifest differs from pinned release/commit; update config/llama-release.json together with benchmark.yaml"
        )
    return data


def detect_environment(backend="auto"):
    system = platform.system()
    machine = platform.machine().lower()
    arch = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(
        machine
    )
    if system not in ("Darwin", "Windows", "Linux") or arch is None:
        raise RuntimeError(f"No official binary configured for {system}/{machine}")
    cuda = None
    if (
        system != "Darwin"
        and backend in ("auto", "cuda")
        and shutil.which("nvidia-smi")
    ):
        result = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            raise RuntimeError(
                "nvidia-smi failed; repair the NVIDIA driver or select backend: cpu explicitly"
            )
        if result.returncode == 0:
            match = re.search(r"CUDA Version:\s*(\d+)\.(\d+)", result.stdout)
            if not match:
                raise RuntimeError(
                    "NVIDIA detected, but nvidia-smi did not report supported CUDA version; update the NVIDIA driver"
                )
            cuda = (int(match[1]), int(match[2]))
    if backend == "auto":
        backend = (
            "metal"
            if system == "Darwin" and arch == "arm64"
            else ("cuda" if cuda else "cpu")
        )
    if backend not in ("cpu", "metal", "cuda"):
        raise RuntimeError("backend must be auto, cpu, metal or cuda")
    if backend == "metal" and system != "Darwin":
        raise RuntimeError("Metal requires macOS")
    if backend == "cuda" and cuda is None:
        raise RuntimeError(
            "CUDA requires an NVIDIA GPU and working NVIDIA driver (nvidia-smi); CUDA Toolkit is not required"
        )
    return {"os": system, "arch": arch, "backend": backend, "driver_cuda": cuda}


def select_assets(data, env):
    tag = data["binary_release"]
    system, arch, backend = env["os"], env["arch"], env["backend"]
    assets = {asset["name"]: asset for asset in data["assets"]}
    cuda_version = None
    if system == "Darwin":
        names = [f"llama-{tag}-bin-macos-{arch}.tar.gz"]
    else:
        target = "win" if system == "Windows" else "ubuntu"
        suffix = ".zip" if system == "Windows" else ".tar.gz"
        if backend == "cuda":
            candidates = []
            pattern = re.compile(
                rf"llama-{re.escape(tag)}-bin-{target}-cuda-(\d+)\.(\d+)-{arch}{re.escape(suffix)}$"
            )
            for name in assets:
                match = pattern.fullmatch(name)
                if match and (int(match[1]), int(match[2])) <= tuple(
                    env["driver_cuda"]
                ):
                    candidates.append(((int(match[1]), int(match[2])), name))
            if not candidates:
                raise RuntimeError(
                    f"No official CUDA binary for {system}/{arch} compatible with driver CUDA {env['driver_cuda']}; update the NVIDIA driver"
                )
            version, main = max(candidates)
            cuda_version = ".".join(map(str, version))
            runtime = (
                f"cudart-llama-bin-win-cuda-{cuda_version}-{arch}.zip"
                if system == "Windows"
                else "cudart-" + main
            )
            names = [main, runtime]
        else:
            names = [
                f"llama-{tag}-bin-win-cpu-{arch}.zip"
                if system == "Windows"
                else f"llama-{tag}-bin-ubuntu-{arch}.tar.gz"
            ]
    if any(name not in assets for name in names):
        raise RuntimeError(
            f"No official release artifact configured for {env}: {names}; compilation is not supported"
        )
    return [assets[name] for name in names], cuda_version


def download(asset):
    path = ROOT / ".cache/downloads" / asset["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and sha(path) == asset["sha256"]:
        return path
    temporary = path.with_suffix(path.suffix + ".part")
    print("Downloading official artifact:", asset["name"], flush=True)
    try:
        with httpx.stream(
            "GET", asset["url"], follow_redirects=True, timeout=120
        ) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for block in response.iter_bytes(1024 * 1024):
                    output.write(block)
        if sha(temporary) != asset["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch: {asset['name']}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def extract(archive, destination):
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (destination / member.filename).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RuntimeError("Unsafe archive path")
            package.extractall(destination)
    else:
        with tarfile.open(archive) as package:
            package.extractall(destination, filter="data")


def validate_executable(executable, commit, backend):
    try:
        version = run([executable, "--version"], stderr=subprocess.STDOUT)
        if commit[:7] not in version:
            raise RuntimeError("Official binary reports a different commit: " + version)
        devices = run([executable, "--list-devices"], stderr=subprocess.STDOUT)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", str(exc))
        raise RuntimeError(
            "Official binary cannot run. Check OS/glibc compatibility, NVIDIA driver or Windows Visual C++ runtime. No compilation fallback.\n"
            + detail
        ) from exc
    expected = "CUDA" if backend == "cuda" else "MTL"
    if backend != "cpu" and expected not in devices:
        raise RuntimeError(
            f"Official binary does not expose the requested {backend} device:\n{devices}"
        )
    return version, devices


def install(c):
    data = manifest(c)
    env = detect_environment(c["server"]["backend"])
    assets, cuda = select_assets(data, env)
    label = f"{env['os'].lower()}-{env['arch']}-{env['backend']}" + (
        f"-{cuda}" if cuda else ""
    )
    destination = ROOT / "runtime/llama.cpp" / data["binary_release"] / label
    executable_name = "llama-server.exe" if env["os"] == "Windows" else "llama-server"
    executable = destination / executable_name
    lockpath = destination / "install.json"
    expected = {asset["name"]: asset["sha256"] for asset in assets}
    if lockpath.exists():
        previous = json.loads(lockpath.read_text(encoding="utf-8"))
        if (
            previous.get("asset_sha256") == expected
            and executable.exists()
            and sha(executable) == previous.get("executable_sha256")
        ):
            version, devices = validate_executable(
                executable, data["commit"], env["backend"]
            )
            dump(ROOT / "config/runtime.json", previous)
            print("Official binary already installed:", executable)
            return previous
    archives = [download(asset) for asset in assets]
    temporary_root = ROOT / ".cache"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=temporary_root, prefix="llama-release-"
    ) as folder:
        folder = Path(folder)
        main = folder / "main"
        extract(archives[0], main)
        matches = list(main.rglob(executable_name))
        if len(matches) != 1:
            raise RuntimeError(
                "Release archive must contain exactly one llama-server executable"
            )
        candidate = matches[0]
        if len(archives) == 2:
            runtime = folder / "cuda-runtime"
            extract(archives[1], runtime)
            libraries = [
                p
                for p in runtime.rglob("*")
                if p.is_file() and (p.suffix.lower() == ".dll" or ".so" in p.name)
            ]
            if not libraries:
                raise RuntimeError("CUDA runtime archive contains no shared libraries")
            for library in libraries:
                shutil.copy2(library, candidate.parent / library.name)
        version, devices = validate_executable(
            candidate, data["commit"], env["backend"]
        )
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidate.parent, destination, symlinks=True)
    info = {
        "kind": "official_release",
        "release": data["stable_release"],
        "binary_release": data["binary_release"],
        "commit": data["commit"],
        "backend": env["backend"],
        "environment": env,
        "cuda_runtime": cuda,
        "executable": executable.relative_to(ROOT).as_posix(),
        "executable_sha256": sha(executable),
        "asset_sha256": expected,
        "asset_urls": [asset["url"] for asset in assets],
        "version": version,
        "devices": devices,
    }
    dump(lockpath, info)
    dump(ROOT / "config/runtime.json", info)
    return info


def verify(c):
    path = ROOT / "config/runtime.json"
    if not path.exists():
        raise RuntimeError(
            "Official llama.cpp binary is not installed. Run scripts/setup first."
        )
    info = json.loads(path.read_text(encoding="utf-8"))
    executable = (ROOT / info["executable"]).resolve()
    if (
        not executable.is_relative_to((ROOT / "runtime").resolve())
        or info["commit"] != c["llama"]["commit"]
        or info["release"] != c["llama"]["release"]
    ):
        raise RuntimeError(
            "Installed runtime differs from pinned configuration; run setup again"
        )
    if not executable.exists() or sha(executable) != info["executable_sha256"]:
        raise RuntimeError("Installed binary checksum mismatch; run setup again")
    validate_executable(executable, info["commit"], info["backend"])
    return info
