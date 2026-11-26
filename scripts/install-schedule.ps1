param([string]$TaskName = 'LinfyeDailyNews')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '请先按 README 创建 .venv 并安装依赖。'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot '.env'))) {
    throw '请先运行 .venv/Scripts/python.exe -m newsdaily setup 配置 API。'
}
# 每 5 分钟检查一次到期状态；实际出报时间由 config.json 的 IANA 时区决定。
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument '-m newsdaily due' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description '每日抓取中英文新闻并 AI 选编；时间与时区由项目 config.json 控制' -Force | Out-Null
Write-Host "已注册 $TaskName；电脑开机且当前用户登录时执行。修改 config.json 即可调整时间。"
