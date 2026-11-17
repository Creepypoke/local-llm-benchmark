$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& .\.venv\Scripts\python.exe -m llbench.manage stop @args
if ($LASTEXITCODE -ne 0) { throw "Operation failed; inspect logs" }
