@echo off
rem Creates "iLab Free" shortcut on the Desktop and in the Start menu
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = (Get-Location).Path;" ^
  "$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source;" ^
  "if (-not $pyw) { $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source };" ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'));" ^
  "foreach ($p in $places) {" ^
  "  $s = $ws.CreateShortcut((Join-Path $p 'iLab Free.lnk'));" ^
  "  if ($pyw) { $s.TargetPath = $pyw; $s.Arguments = '\"' + (Join-Path $dir 'run.py') + '\"' }" ^
  "  else { $s.TargetPath = (Join-Path $dir 'start.bat') };" ^
  "  $s.WorkingDirectory = $dir;" ^
  "  $s.IconLocation = (Join-Path $dir 'ilab.ico');" ^
  "  $s.Description = 'iLab Free - digital lab';" ^
  "  $s.Save() };" ^
  "Write-Host 'Shortcut created: Desktop + Start menu'"
pause
