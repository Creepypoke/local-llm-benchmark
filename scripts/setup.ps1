$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $script:ProjectRoot
$script:PythonBootstrap = Get-Content -LiteralPath (Join-Path $script:ProjectRoot 'config\python-windows.json') -Raw | ConvertFrom-Json

function Test-PythonCommand {
    param([string]$Executable, [string[]]$LeadingArguments = @())
    $major = [int]$script:PythonBootstrap.minimum_major
    $minor = [int]$script:PythonBootstrap.minimum_minor
    $probe = "import sys, venv, ensurepip; sys.exit(1) if sys.version_info < ($major, $minor) else print(getattr(sys, '_base_executable', sys.executable))"
    try {
        $output = & $Executable @LeadingArguments -c $probe 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            $path = ([string]@($output)[-1]).Trim()
            if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        }
    } catch {
        # Missing, outdated or incomplete interpreters are not usable for venv.
    }
    return $null
}

function Find-Python {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $path = Test-PythonCommand -Executable $launcher.Source -LeadingArguments @('-3')
        if ($path) { return $path }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $name -All -ErrorAction SilentlyContinue)) {
            # Do not invoke Windows Store placeholder aliases.
            $isStoreAlias = $command.Source -match '[\\/]WindowsApps[\\/]'
            $storeInstalled = $false
            if ($isStoreAlias -and (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue)) {
                $storeInstalled = @(Get-AppxPackage -Name 'PythonSoftwareFoundation.Python*' -ErrorAction SilentlyContinue).Count -gt 0
            }
            if ($command.Source -and (-not $isStoreAlias -or $storeInstalled)) {
                $path = Test-PythonCommand -Executable $command.Source
                if ($path) { return $path }
            }
        }
    }
    # Python can be installed without being present in this terminal's PATH.
    foreach ($root in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($version in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $key = Get-Item -LiteralPath (Join-Path $version.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if ($key) {
                $executable = $key.GetValue('ExecutablePath')
                if (-not $executable -and $key.GetValue('')) {
                    $executable = Join-Path ($key.GetValue('')) 'python.exe'
                }
                if ($executable) {
                    $path = Test-PythonCommand -Executable $executable
                    if ($path) { return $path }
                }
            }
        }
    }
    return $null
}

function Install-Python {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'Python 3.12+ is missing and winget is unavailable. Install Microsoft App Installer (https://aka.ms/getwinget), then rerun setup.'
    }
    $package = $script:PythonBootstrap.winget_package
    Write-Host "Python 3.12+ not found. Installing $package with winget..."
    & $winget.Source install --id $package --exact --source winget --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "Python installation with winget failed (exit code $LASTEXITCODE)." }
    # Preserve this terminal's PATH and add the updated machine/user entries.
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$env:Path;$machinePath;$userPath"
    $python = Find-Python
    if (-not $python) { throw 'winget finished, but Python 3.12+ was not found. Check the Python installation and rerun setup.' }
    return $python
}

function Resolve-Python {
    $python = Find-Python
    if ($python) { return $python }
    return Install-Python
}

function Invoke-Setup {
    param([string[]]$SetupArguments = @())
    $python = Resolve-Python
    Write-Host "Using Python: $python"
    & $python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'venv failed' }
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    & .\.venv\Scripts\python.exe -m llbench.manage setup @SetupArguments
    if ($LASTEXITCODE -ne 0) { throw 'Operation failed; inspect logs' }
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-Setup -SetupArguments $args }
