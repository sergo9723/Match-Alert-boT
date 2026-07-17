# start_bot.ps1 — Windows-аналог start_bot.sh: одна команда,
# поднимает Chrome с отладочным портом (если ещё не поднят),
# ставит недостающие пакеты и запускает бота с логом в файл.
#
# ПЕРВЫЙ ЗАПУСК: откроется окно Chrome — залогинься на 7777.md руками,
#   потом нажми Enter в PowerShell. Дальше бот стартует сам.
# ПОСЛЕДУЮЩИЕ ЗАПУСКИ: если Chrome уже открыт на порту 9222 (и ты уже
#   залогинен) — скрипт это увидит и сразу запустит бота.
#
# ЗАПУСК (в PowerShell, из папки с ботом):
#   .\start_bot.ps1
# Если ругается на "выполнение скриптов отключено" — один раз:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ChromePort = 9222
$ProfileDir = "$env:USERPROFILE\.selenium-chrome-7777"
$BotDir     = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-PortOpen {
    param([int]$Port)
    try {
        $client  = New-Object System.Net.Sockets.TcpClient
        $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $connect.AsyncWaitHandle.WaitOne(500, $false) -and $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

function Find-Chrome {
    $candidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path $p) { return $p }
    }
    $reg = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" -ErrorAction SilentlyContinue).'(default)'
    if ($reg -and (Test-Path $reg)) { return $reg }
    return $null
}

function Get-PythonCmd {
    foreach ($cmd in @("python", "py")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) { return $cmd }
    }
    return $null
}

$PyCmd = Get-PythonCmd
if (-not $PyCmd) {
    Write-Host "❌ Python не найден в PATH. Установи Python 3 (python.org) и перезапусти."
    exit 1
}

# Проверяем/ставим недостающие пакеты (selenium, requests) — на Windows
# без ограничений externally-managed-environment, ставится напрямую.
$check = & $PyCmd -c "import selenium, requests" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "📦 Ставлю недостающие пакеты (selenium, requests)..."
    & $PyCmd -m pip install selenium requests
}

if (Test-PortOpen -Port $ChromePort) {
    Write-Host "✅ Chrome уже слушает порт $ChromePort — пропускаю запуск браузера."
} else {
    $ChromeBin = Find-Chrome
    if (-not $ChromeBin) {
        Write-Host "❌ Не нашёл Chrome. Установи Google Chrome (google.com/chrome)."
        exit 1
    }
    Write-Host "🌐 Открываю $ChromeBin с отладочным портом $ChromePort..."
    New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
    Start-Process $ChromeBin -ArgumentList "--remote-debugging-port=$ChromePort", "--user-data-dir=$ProfileDir"
    Start-Sleep -Seconds 5
    Write-Host ""
    Write-Host "⚠️  ЗАЛОГИНЬСЯ на 7777.md в открывшемся окне Chrome."
    Read-Host "    Когда залогинишься — нажми Enter здесь, чтобы продолжить"
}

Write-Host ""
Write-Host "🚀 Запускаю бота (лог: $BotDir\data\bot.log)..."
New-Item -ItemType Directory -Force -Path "$BotDir\data" | Out-Null
Set-Location $BotDir
& $PyCmd -u sport_bot_v19.py 2>&1 | Tee-Object -FilePath "data\bot.log" -Append
