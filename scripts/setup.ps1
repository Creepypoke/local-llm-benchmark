$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
py -3 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'venv failed' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe -m llbench.manage setup @args
if ($LASTEXITCODE -ne 0) { throw "Operation failed; inspect logs" }
