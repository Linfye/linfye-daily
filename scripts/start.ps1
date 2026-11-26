$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONUTF8 = '1'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先按照 README 创建 .venv 并安装 requirements.txt。'
}
while ($true) {
    Write-Host "`nLinfye 日报"
    Write-Host '1. 输入 / 修改 AI API'
    Write-Host '2. 测试 API 连接'
    Write-Host '3. 立即生成日报'
    Write-Host '4. 打开最新日报'
    Write-Host '5. 抓取并检查媒体来源（不调用 AI）'
    Write-Host '6. 安装 / 更新每日定时任务'
    Write-Host '0. 退出'
    $choice = Read-Host '选择'
    switch ($choice) {
        '1' { & $pythonPath -m newsdaily setup }
        '2' { & $pythonPath -m newsdaily test-api }
        '3' { & $pythonPath -m newsdaily run }
        '4' {
            $reportFile = Get-ChildItem -LiteralPath (Join-Path $projectRoot 'output') -Filter '*.md' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
            if ($reportFile) { Invoke-Item -LiteralPath $reportFile.FullName }
            else { Write-Host '还没有日报，请先配置 API 并生成。' }
        }
        '5' { & $pythonPath -m newsdaily fetch }
        '6' { & (Join-Path $PSScriptRoot 'install-schedule.ps1') }
        '0' { return }
        default { Write-Host '请输入 0–6。' }
    }
}
