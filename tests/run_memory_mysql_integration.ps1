$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$gitCommon = git -C $repoRoot rev-parse --git-common-dir
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the repository common Git directory"
}
$gitCommon = $gitCommon.Trim()
if (-not [System.IO.Path]::IsPathRooted($gitCommon)) {
    $gitCommon = Join-Path $repoRoot $gitCommon
}
$gitCommon = [System.IO.Path]::GetFullPath($gitCommon)
$mainRoot = Split-Path -Parent $gitCommon
$python = Join-Path $mainRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "The project Python virtual environment is unavailable"
}

$mysqld = (Get-Command mysqld.exe -ErrorAction Stop).Source
$mysqlBase = Split-Path -Parent (Split-Path -Parent $mysqld)
$mysqladmin = Join-Path $mysqlBase "bin\mysqladmin.exe"
$tempParent = [System.IO.Path]::GetFullPath(
    [System.IO.Path]::GetTempPath()
).TrimEnd("\")
$suffix = [guid]::NewGuid().ToString("N").Substring(0, 20)
$tempRoot = Join-Path $tempParent "astrarelay-memory-mysql8-$suffix"
$dataDir = Join-Path $tempRoot "data"
$initLog = Join-Path $tempRoot "initialize.log"
$serverLog = Join-Path $tempRoot "server.log"
$pidFile = Join-Path $tempRoot "mysqld.pid"

$listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
if ($port -lt 49152 -or $port -gt 65535) {
    throw "Random port is outside the required high range"
}

$serverProcess = $null
$testExit = 1
$cleanup = @()

try {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    $resolvedRoot = (Resolve-Path -LiteralPath $tempRoot).Path
    if (-not $resolvedRoot.StartsWith(
        $tempParent + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Temporary MySQL directory escaped the system temp root"
    }
    if ((Split-Path -Leaf $resolvedRoot) -notmatch (
        "^astrarelay-memory-mysql8-[0-9a-f]{20}$"
    )) {
        throw "Temporary MySQL directory name failed validation"
    }

    & $mysqld `
        --no-defaults `
        --initialize-insecure `
        "--basedir=$mysqlBase" `
        "--datadir=$dataDir" `
        "--log-error=$initLog"
    if ($LASTEXITCODE -ne 0) {
        throw "mysqld initialization failed with exit code $LASTEXITCODE"
    }

    $argumentList = @(
        "--no-defaults",
        "--standalone",
        "--basedir=`"$mysqlBase`"",
        "--datadir=`"$dataDir`"",
        "--port=$port",
        "--bind-address=127.0.0.1",
        "--mysqlx=0",
        "--pid-file=`"$pidFile`"",
        "--log-error=`"$serverLog`""
    )
    $serverProcess = Start-Process `
        -FilePath $mysqld `
        -ArgumentList $argumentList `
        -PassThru `
        -WindowStyle Hidden

    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($serverProcess.HasExited) {
            throw "Temporary mysqld exited early with code $($serverProcess.ExitCode)"
        }
        & $mysqladmin `
            --no-defaults `
            --protocol=TCP `
            --host=127.0.0.1 `
            "--port=$port" `
            --user=root `
            ping `
            --silent 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready) {
        throw "Temporary mysqld did not become ready"
    }

    $env:ASTRARELAY_RUN_MEMORY_MYSQL_TESTS = "1"
    $env:ASTRARELAY_MEMORY_MYSQL_TEST_PORT = [string]$port
    & $python -m pytest tests/test_memory_mysql_integration.py -q
    $testExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:ASTRARELAY_RUN_MEMORY_MYSQL_TESTS `
        -ErrorAction SilentlyContinue
    Remove-Item Env:ASTRARELAY_MEMORY_MYSQL_TEST_PORT `
        -ErrorAction SilentlyContinue

    if ($null -ne $serverProcess -and -not $serverProcess.HasExited) {
        $actual = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $($serverProcess.Id)"
        if (
            $null -eq $actual -or
            $actual.ExecutablePath -ne $mysqld -or
            $actual.CommandLine -notlike "*$dataDir*"
        ) {
            throw "Refusing to stop an unverified process"
        }
        & $mysqladmin `
            --no-defaults `
            --protocol=TCP `
            --host=127.0.0.1 `
            "--port=$port" `
            --user=root `
            shutdown 2>$null
        $serverProcess.WaitForExit(10000) | Out-Null
        if (-not $serverProcess.HasExited) {
            Stop-Process -Id $serverProcess.Id -Force
            $serverProcess.WaitForExit(10000) | Out-Null
        }
    }

    $listening = Get-NetTCPConnection `
        -LocalAddress 127.0.0.1 `
        -LocalPort $port `
        -State Listen `
        -ErrorAction SilentlyContinue
    if ($listening) {
        throw "Temporary MySQL port is still listening after cleanup"
    }
    $cleanup += "port_closed:$port"

    if (Test-Path -LiteralPath $tempRoot) {
        $resolvedRoot = (Resolve-Path -LiteralPath $tempRoot).Path
        if (-not $resolvedRoot.StartsWith(
            $tempParent + "\",
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Refusing to delete a path outside the system temp root"
        }
        if ((Split-Path -Leaf $resolvedRoot) -notmatch (
            "^astrarelay-memory-mysql8-[0-9a-f]{20}$"
        )) {
            throw "Refusing to delete an unvalidated temporary MySQL directory"
        }
        Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
    }
    if (Test-Path -LiteralPath $tempRoot) {
        throw "Temporary MySQL directory still exists after cleanup"
    }
    $cleanup += "temp_directory_removed:true"
    Write-Output ($cleanup -join "`n")
}

exit $testExit
