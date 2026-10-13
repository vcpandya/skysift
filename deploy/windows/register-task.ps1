param([string]$Source = "kbonus_bkg", [int]$Limit = 2000, [string]$At = "02:00")
# Run the module through the virtualenv interpreter; never register a console-script launcher
# (antivirus software sandboxes freshly built executables).
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "-m skysift run --source $Source --limit $Limit" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
Register-ScheduledTask -TaskName "SkySift nightly sweep" -Action $action -Trigger $trigger -Description "SkySift archival sweep" -Force
