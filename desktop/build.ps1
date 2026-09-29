$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create desktop/.venv and install requirements-build.txt first.' }
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$assets = Join-Path $projectRoot 'kosika\workbench_assets'
& $python -m PyInstaller --noconfirm --onedir --console --name KosikaHost --distpath host-dist --workpath host-build --specpath host-build --paths $projectRoot --add-data "${assets}:kosika/workbench_assets" (Join-Path $projectRoot 'kosika\packaged_host.py')
if ($LASTEXITCODE -ne 0) { throw 'Bundled host build failed.' }
& npm.cmd run dist
if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed.' }
Get-ChildItem release\*.exe | Select-Object Name, Length
