# Live demo of the hands' eyes: Kosika looks at Notepad's window, finds the
# "View" menu by its name, and clicks it, asking you before each action.
# Notepad must be open. Answer y to each; hold Escape to stop it.
$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$env:PYTHONIOENCODING = "utf-8"
$target = Join-Path $env:TEMP "kosika-hands-target.json"
[IO.File]::WriteAllText($target, '{"t": {"app": "notepad", "name": "View"}}')
Set-Location $PSScriptRoot
python -m kosika.cli run main.kos --workflow press --input $target --allow-shell --allow-folder . --approve --approved-by Aryan
