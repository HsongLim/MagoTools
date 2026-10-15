# 윈도우 설치 파일 만들기.  저장소 맨 위 폴더에서 (PowerShell):
#   powershell -ExecutionPolicy Bypass -File build\build_windows.ps1
# 필요: Python 3.12 (python.org), NSIS (makensis) - https://nsis.sourceforge.io
# 결과: dist\ShotPicker_Setup_<버전>.exe
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Root = (Get-Location).Path

$ver = (Select-String -Path src\shot_picker.py -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Shot Picker $ver 빌드 (Windows)"

python -m pip install -q --upgrade pip pyinstaller pillow tkinterdnd2 certifi

$work = Join-Path $env:TEMP ("sp_build_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $work | Out-Null
try {
  python -m PyInstaller --noconfirm --clean --onefile --windowed --name ShotPicker `
    --distpath "$work\dist" --workpath "$work\build" --specpath "$work" `
    --icon "$Root\assets\ShotPicker.ico" --add-data "$Root\assets\ShotPicker.ico;." `
    --collect-all tkinterdnd2 src\shot_picker.py
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller 실패" }

  $p = "$work\payload"
  New-Item -ItemType Directory -Force $p | Out-Null
  Copy-Item "$work\dist\ShotPicker.exe" $p
  Copy-Item assets\ShotPicker.ico $p
  # 사용법은 앱 안 설명서(help_text)에서 만들어서 항상 같게
  python -c "import sys; sys.path.insert(0, 'src'); import shot_picker as g; open(sys.argv[1], 'w', encoding='utf-8-sig').write(g.help_text(False))" "$p\README.txt"
  if ($LASTEXITCODE -ne 0) { Copy-Item config\README_windows.txt "$p\README.txt" }

  New-Item -ItemType Directory -Force dist | Out-Null
  $makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
  if (-not $makensis) { $makensis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
  & $makensis -V2 "-DVERSION=$ver" "-DSRC=$p" "-DOUT=$Root\dist\ShotPicker_Setup_$ver.exe" build\installer.nsi
  if ($LASTEXITCODE -ne 0) { throw "makensis 실패" }
  Write-Host "완료: dist\ShotPicker_Setup_$ver.exe"
}
finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
