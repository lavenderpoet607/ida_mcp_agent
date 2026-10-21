param(
    [string]$Repo = "lavenderpoet607/ida_mcp_agent",
    [string]$Branch = "main",
    [string]$InstallDir = "",
    [string]$IdaPluginsDir = "",
    [string]$ReClassDir = "",
    [switch]$InstallReClass = $false,
    [switch]$WithReClass = $false
)

$ErrorActionPreference = "Stop"

$enableReclass = $InstallReClass -or $WithReClass

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "   IDA PRO & RECLASS.NET MCP INSTALLER (POWERSHELL)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$pyVersion = (& python --version 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python tidak terdeteksi di PATH. Silakan install Python 3.11+ terlebih dahulu."
    exit 1
}
Write-Host "[1/5] Python terdeteksi: $pyVersion" -ForegroundColor Green

Write-Host "[2/5] Menginstall Python dependencies..." -ForegroundColor Yellow
& python -m pip install --upgrade pip
& python -m pip install ida-pro-mcp mcp requests urllib3
Write-Host "[2/5] Dependencies berhasil diinstall." -ForegroundColor Green

if (-not $InstallDir) {
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "rea_agent.py"))) {
        $InstallDir = $PSScriptRoot
    } elseif (Test-Path "$env:USERPROFILE\Documents\project\ida_mcp_agent\rea_agent.py") {
        $InstallDir = "$env:USERPROFILE\Documents\project\ida_mcp_agent"
    } else {
        $winDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Windows)
        $currentPath = $PWD.Path
        if (-not $currentPath -or $currentPath.StartsWith($winDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            $docDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::MyDocuments)
            $InstallDir = Join-Path $docDir "ida_mcp_agent"
        } else {
            $InstallDir = Join-Path $currentPath "ida_mcp_agent"
        }
    }
}

$isLocal = $false
$baseDir = $InstallDir
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "rea_agent.py"))) {
    $isLocal = $true
    $baseDir = $PSScriptRoot
} elseif (Test-Path (Join-Path $InstallDir "rea_agent.py")) {
    $isLocal = $true
    $baseDir = $InstallDir
}


if (-not $isLocal) {
    Write-Host "[3/5] Mengunduh asset dari GitHub: $Repo ($Branch)..." -ForegroundColor Yellow
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    }

    $rawBase = "https://raw.githubusercontent.com/$Repo/$Branch"
    $filesToDownload = @(
        @{ Url = "$rawBase/rea_agent.py"; Out = (Join-Path $InstallDir "rea_agent.py") },
        @{ Url = "$rawBase/ida_agent_config.json"; Out = (Join-Path $InstallDir "ida_agent_config.json") },
        @{ Url = "$rawBase/ida-mcp.json"; Out = (Join-Path $InstallDir "ida-mcp.json") },
        @{ Url = "$rawBase/idapythonrc.py"; Out = (Join-Path $InstallDir "idapythonrc.py") }
    )

    if ($enableReclass) {
        $binDir = Join-Path $InstallDir "bin"
        if (-not (Test-Path $binDir)) {
            New-Item -ItemType Directory -Force -Path $binDir | Out-Null
        }
        $filesToDownload += @(
            @{ Url = "$rawBase/reclass_mcp_server.py"; Out = (Join-Path $InstallDir "reclass_mcp_server.py") },
            @{ Url = "$rawBase/bin/ReClassMCP.dll"; Out = (Join-Path $binDir "ReClassMCP.dll") },
            @{ Url = "$rawBase/bin/Newtonsoft.Json.dll"; Out = (Join-Path $binDir "Newtonsoft.Json.dll") }
        )
    }

    foreach ($item in $filesToDownload) {
        Write-Host "  -> Mengunduh $($item.Url)" -ForegroundColor DarkGray
        Invoke-WebRequest -Uri $item.Url -OutFile $item.Out -UseBasicParsing
    }
}

Write-Host "[4/5] Mencari dan memasang IDA Pro MCP Plugin (Auto-Find)..." -ForegroundColor Yellow
$idaPluginTargets = @()
if ($IdaPluginsDir -and (Test-Path $IdaPluginsDir)) {
    $idaPluginTargets += $IdaPluginsDir
}

$runningIda = Get-Process -Name "ida", "ida64", "idat", "idat64" -ErrorAction SilentlyContinue
if ($runningIda) {
    foreach ($proc in $runningIda) {
        if ($proc.Path) {
            $pFolder = Split-Path -Parent $proc.Path
            $pPlugin = Join-Path $pFolder "plugins"
            if (-not (Test-Path $pPlugin)) {
                New-Item -ItemType Directory -Force -Path $pPlugin | Out-Null
            }
            $idaPluginTargets += $pPlugin
        }
    }
}

$appDataIda = "$env:APPDATA\Hex-Rays\IDA Pro\plugins"
if (-not (Test-Path $appDataIda)) {
    New-Item -ItemType Directory -Force -Path $appDataIda | Out-Null
}
$idaPluginTargets += $appDataIda

$searchRoots = @(
    $env:USERPROFILE,
    ${env:ProgramFiles},
    ${env:ProgramFiles(x86)},
    "$env:LOCALAPPDATA\Programs"
)
foreach ($root in $searchRoots) {
    if ($root -and (Test-Path $root)) {
        $idaDirs = Get-ChildItem -Path $root -Filter "*ida*" -Directory -Depth 2 -ErrorAction SilentlyContinue
        foreach ($d in $idaDirs) {
            $hasExe = (Test-Path (Join-Path $d.FullName "ida.exe")) -or (Test-Path (Join-Path $d.FullName "ida64.exe"))
            $hasPlugins = Test-Path (Join-Path $d.FullName "plugins")
            if ($hasExe -or $hasPlugins) {
                $targetP = Join-Path $d.FullName "plugins"
                if (-not (Test-Path $targetP)) {
                    New-Item -ItemType Directory -Force -Path $targetP | Out-Null
                }
                $idaPluginTargets += $targetP
            }
        }
    }
}

$sourcePlugin = Join-Path $baseDir "rea_agent.py"
$sourceConfig = Join-Path $baseDir "ida_agent_config.json"
$sourceRc = Join-Path $baseDir "idapythonrc.py"
$uniqueIdaTargets = $idaPluginTargets | Select-Object -Unique
foreach ($targetDir in $uniqueIdaTargets) {
    Copy-Item -Path $sourcePlugin -Destination (Join-Path $targetDir "rea_agent.py") -Force
    if (Test-Path $sourceConfig) {
        Copy-Item -Path $sourceConfig -Destination (Join-Path $targetDir "ida_agent_config.json") -Force
    }
    Write-Host "  [+] IDA Plugin terpasang di: $targetDir\rea_agent.py" -ForegroundColor Green
    $cfgPath = Join-Path $targetDir "plugins.cfg"
    if (Test-Path $cfgPath) {
        $cfgLines = Get-Content $cfgPath
        $newCfg = $cfgLines | Where-Object { $_ -notmatch 'MCP_Dashboard' }
        $newCfg += "MCP_Dashboard rea_agent Ctrl-Alt-M 0 GUI SILENT"
        $newCfg | Set-Content $cfgPath -Encoding ascii
        Write-Host "  [+] Entri MCP_Dashboard diperbarui di plugins.cfg" -ForegroundColor Green
    }
    $idaParent = Split-Path -Parent $targetDir
    if (Test-Path $sourceRc) {
        Copy-Item -Path $sourceRc -Destination (Join-Path $idaParent "idapythonrc.py") -Force
        $idaPyDir = Join-Path $idaParent "python"
        if (Test-Path $idaPyDir) {
            Copy-Item -Path $sourceRc -Destination (Join-Path $idaPyDir "idapythonrc.py") -Force
        }
    }
}
$appDataRoot = "$env:APPDATA\Hex-Rays\IDA Pro"
if ((Test-Path $sourceRc) -and (Test-Path $appDataRoot)) {
    Copy-Item -Path $sourceRc -Destination (Join-Path $appDataRoot "idapythonrc.py") -Force
    Write-Host "  [+] Auto-load terpasang di: $appDataRoot\idapythonrc.py" -ForegroundColor Green
}

if ($enableReclass) {
    Write-Host "[5/5] Mencari dan memasang ReClass.NET MCP Plugin (Auto-Find)..." -ForegroundColor Yellow
    $reclassPluginTargets = @()
    if ($ReClassDir -and (Test-Path $ReClassDir)) {
        $pDir = Join-Path $ReClassDir "Plugins"
        if (-not (Test-Path $pDir)) { New-Item -ItemType Directory -Force -Path $pDir | Out-Null }
        $reclassPluginTargets += $pDir
    }

    $runningReclass = Get-Process -Name "ReClass.NET" -ErrorAction SilentlyContinue
    if ($runningReclass) {
        foreach ($rProc in $runningReclass) {
            if ($rProc.Path) {
                $rFolder = Split-Path -Parent $rProc.Path
                $rPlugin = Join-Path $rFolder "Plugins"
                if (-not (Test-Path $rPlugin)) {
                    New-Item -ItemType Directory -Force -Path $rPlugin | Out-Null
                }
                $reclassPluginTargets += $rPlugin
            }
        }
    }

    $localReclass = Join-Path $baseDir "ReClass.NET\x64\Plugins"
    if (Test-Path (Join-Path $baseDir "ReClass.NET\x64")) {
        if (-not (Test-Path $localReclass)) { New-Item -ItemType Directory -Force -Path $localReclass | Out-Null }
        $reclassPluginTargets += $localReclass
    }

    $reclassSearchRoots = @(
        $baseDir,
        $env:USERPROFILE,
        ${env:ProgramFiles},
        ${env:ProgramFiles(x86)},
        "$env:LOCALAPPDATA\Programs"
    )
    foreach ($rRoot in $reclassSearchRoots) {
        if ($rRoot -and (Test-Path $rRoot)) {
            $rcDirs = Get-ChildItem -Path $rRoot -Filter "*ReClass*" -Directory -Depth 3 -ErrorAction SilentlyContinue
            foreach ($rc in $rcDirs) {
                if (Test-Path (Join-Path $rc.FullName "ReClass.NET.exe")) {
                    $targetRP = Join-Path $rc.FullName "Plugins"
                    if (-not (Test-Path $targetRP)) {
                        New-Item -ItemType Directory -Force -Path $targetRP | Out-Null
                    }
                    $reclassPluginTargets += $targetRP
                }
            }
        }
    }

    $dllSource = Join-Path $baseDir "bin\ReClassMCP.dll"
    $jsonSource = Join-Path $baseDir "bin\Newtonsoft.Json.dll"
    if (-not (Test-Path $dllSource) -and (Test-Path (Join-Path $baseDir "ReClass.NET-MCP\release\x64\ReClassMCP.dll"))) {
        $dllSource = Join-Path $baseDir "ReClass.NET-MCP\release\x64\ReClassMCP.dll"
        $jsonSource = Join-Path $baseDir "ReClass.NET-MCP\release\x64\Newtonsoft.Json.dll"
    }

    $uniqueReclassTargets = $reclassPluginTargets | Select-Object -Unique
    foreach ($targetPDir in $uniqueReclassTargets) {
        try {
            Copy-Item -Path $dllSource -Destination (Join-Path $targetPDir "ReClassMCP.dll") -Force -ErrorAction Stop
            Copy-Item -Path $jsonSource -Destination (Join-Path $targetPDir "Newtonsoft.Json.dll") -Force -ErrorAction Stop
            Write-Host "  [+] ReClass Plugin terpasang di: $targetPDir" -ForegroundColor Green
        } catch {
            Write-Host "  [*] ReClass Plugin sudah aktif/terkunci di: $targetPDir" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[5/5] Instalasi ReClass.NET dilewati (Gunakan flag -InstallReClass untuk memasang)" -ForegroundColor DarkGray
}

$claudeDir = "$env:APPDATA\Claude"
if (Test-Path $claudeDir) {
    $claudeConfig = Join-Path $claudeDir "claude_desktop_config.json"
    $configObj = @{ mcpServers = @{} }
    if (Test-Path $claudeConfig) {
        try {
            $configObj = (Get-Content $claudeConfig -Raw | ConvertFrom-Json)
            if (-not $configObj.mcpServers) {
                $configObj | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value @{}
            }
        } catch {}
    }
    $idaConfig = @{
        command = "python"
        args = @("-m", "ida_pro_mcp.server", "--ida-rpc", "http://127.0.0.1:13337")
    }
    $configObj.mcpServers | Add-Member -MemberType NoteProperty -Name "ida-pro" -Value $idaConfig -Force

    if ($enableReclass) {
        $reclassServerScript = Join-Path $baseDir "reclass_mcp_server.py"
        $reclassConfig = @{
            command = "python"
            args = @($reclassServerScript)
        }
        $configObj.mcpServers | Add-Member -MemberType NoteProperty -Name "reclass" -Value $reclassConfig -Force
    }

    $configObj | ConvertTo-Json -Depth 10 | Set-Content $claudeConfig -Encoding utf8
    Write-Host "  [+] Konfigurasi Claude Desktop MCP berhasil diperbarui." -ForegroundColor Green
}

Write-Host "==================================================" -ForegroundColor Green
Write-Host "   INSTALASI BERHASIL SELESAI!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host "Endpoint IDA Pro MCP : http://127.0.0.1:13337/mcp" -ForegroundColor Cyan
Write-Host "Plugin IDA Pro       : rea_agent.py" -ForegroundColor Cyan
if ($enableReclass) {
    Write-Host "Endpoint ReClass TCP : 127.0.0.1:27015" -ForegroundColor Cyan
    Write-Host "Plugin ReClass.NET   : ReClassMCP.dll" -ForegroundColor Cyan
} else {
    Write-Host "Plugin ReClass.NET   : Dilewati (opsional, gunakan -InstallReClass)" -ForegroundColor DarkGray
}
