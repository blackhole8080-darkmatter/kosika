param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonPath) {
    $PythonPath = (Get-Command python.exe -ErrorAction Stop).Source
}
$pythonWindowed = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowed -PathType Leaf)) {
    throw 'A Python installation with pythonw.exe is required.'
}
& $PythonPath -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
if ($LASTEXITCODE -ne 0) { throw 'Python version validation failed.' }
$desktopPath = [Environment]::GetFolderPath('Desktop')
$programsPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Kosika'
New-Item -ItemType Directory -Path $programsPath -Force | Out-Null
$shell = New-Object -ComObject WScript.Shell
foreach ($location in @($desktopPath, $programsPath)) {
    foreach ($entry in @(@('Kosika', ''), @('Stop Kosika', ' --stop'))) {
        $target = Join-Path $location ($entry[0] + '.lnk')
        if (Test-Path -LiteralPath $target) {
            $existing = $shell.CreateShortcut($target)
            if ($existing.WorkingDirectory -ne $projectRoot -or $existing.Arguments -notlike '*-m kosika.desktop*') {
                throw "Refusing to replace an unrelated shortcut: $target"
            }
        }
        $shortcut = $shell.CreateShortcut($target)
        $shortcut.TargetPath = $pythonWindowed
        $shortcut.Arguments = '-B -m kosika.desktop' + $entry[1]
        $shortcut.WorkingDirectory = $projectRoot
        $shortcut.Description = 'Kosika - local programming and AI workflow workbench'
        $shortcut.WindowStyle = 7
        $shortcut.Save()
        Write-Output "Installed $target"
    }
}
Write-Output 'Open Kosika from your Desktop or Start menu. No administrator rights or startup task required.'
