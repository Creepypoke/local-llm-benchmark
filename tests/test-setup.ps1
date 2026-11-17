$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\setup.ps1')

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
function Invoke-Case {
    param([string]$Name, [scriptblock]$Body)
    $oldPath = $env:Path
    try { & $Body; Write-Host "PASS: $Name" }
    finally { $env:Path = $oldPath }
}

Invoke-Case 'Existing Python is reused without installation' {
    function Find-Python { return 'C:\Python312\python.exe' }
    function Install-Python { throw 'Installation must not run' }
    Assert-True ((Resolve-Python) -eq 'C:\Python312\python.exe') 'Existing interpreter was not reused'
}

Invoke-Case 'Missing Python triggers installation once' {
    $script:InstallCalls = 0
    function Find-Python { return $null }
    function Install-Python { $script:InstallCalls++; return 'C:\Python313\python.exe' }
    Assert-True ((Resolve-Python) -eq 'C:\Python313\python.exe') 'Installed interpreter was not returned'
    Assert-True ($script:InstallCalls -eq 1) 'Installation was repeated'
}

Invoke-Case 'winget installs per-user and finds Python without a new terminal' {
    $script:WingetArguments = @()
    function Get-Command { param($Name, $ErrorAction); return [pscustomobject]@{ Source = 'Invoke-FakeWinget' } }
    function Invoke-FakeWinget { $script:WingetArguments = @($args); $global:LASTEXITCODE = 0 }
    function Find-Python { return 'C:\Users\Test User\Python313\python.exe' }
    Assert-True ((Install-Python) -eq 'C:\Users\Test User\Python313\python.exe') 'Python was not rediscovered'
    foreach ($argument in @('install', '--id', 'Python.Python.3.13', '--exact', '--source', 'winget', '--scope', 'user', '--silent')) {
        Assert-True ($script:WingetArguments -contains $argument) "Missing winget argument: $argument"
    }
}

Invoke-Case 'Missing winget gives an actionable error' {
    function Get-Command { param($Name, $ErrorAction); return $null }
    $caught = $false
    try { Install-Python } catch { $caught = $_.Exception.Message -like '*App Installer*' }
    Assert-True $caught 'Missing winget was not explained'
}

Invoke-Case 'Failed winget installation stops setup' {
    function Get-Command { param($Name, $ErrorAction); return [pscustomobject]@{ Source = 'Invoke-FakeWinget' } }
    function Invoke-FakeWinget { $global:LASTEXITCODE = 1603 }
    function Find-Python { throw 'Must not continue after installation failure' }
    $caught = $false
    try { Install-Python } catch { $caught = $_.Exception.Message -like '*exit code 1603*' }
    Assert-True $caught 'winget exit code was not checked'
}

Invoke-Case 'Unusable Python after winget is an error' {
    function Get-Command { param($Name, $ErrorAction); return [pscustomobject]@{ Source = 'Invoke-FakeWinget' } }
    function Invoke-FakeWinget { $global:LASTEXITCODE = 0 }
    function Find-Python { return $null }
    $caught = $false
    try { Install-Python } catch { $caught = $_.Exception.Message -like '*Python 3.12+ was not found*' }
    Assert-True $caught 'setup continued without a usable Python'
}

Invoke-Case 'Old or broken Python is rejected' {
    function Invoke-OldPython { $global:LASTEXITCODE = 1 }
    Assert-True ($null -eq (Test-PythonCommand -Executable 'Invoke-OldPython')) 'Old interpreter was accepted'
}

Invoke-Case 'Windows Store placeholder is not invoked' {
    function Get-Command {
        param($Name, [switch]$All, $ErrorAction)
        if ($Name -eq 'python.exe') { return [pscustomobject]@{ Source = 'C:\Users\Test\AppData\Local\Microsoft\WindowsApps\python.exe' } }
        return $null
    }
    function Get-ChildItem { param($LiteralPath, $ErrorAction); return @() }
    function Test-PythonCommand { throw 'Store placeholder was invoked' }
    Assert-True ($null -eq (Find-Python)) 'Store alias was accepted'
}
