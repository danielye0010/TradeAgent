# Current-user WSL lifetime anchor only. Research runs in the separate Linux unit.
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$anchorCommand = '$child=Start-Process -FilePath wsl.exe -ArgumentList @(''-d'',''Ubuntu'',''-u'',''danielye'',''--exec'',''/usr/bin/sleep'',''infinity'') -WindowStyle Hidden -PassThru; $child.WaitForExit(); exit $child.ExitCode'
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -Command "' + $anchorCommand + '"')
$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'TradeAgentProspectiveWSL' -Action $action -Trigger @($logon,$retry) -Principal $principal -Settings $settings -Description 'Keep Ubuntu available for the zero-money TradeAgent shadow user service; no orders or credentials.' -Force | Select-Object TaskName,State
Start-ScheduledTask -TaskName 'TradeAgentProspectiveWSL'
