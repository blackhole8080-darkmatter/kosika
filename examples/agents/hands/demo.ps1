# Live demo of the hands agent: an agent opens Notepad and writes a note
# with its own coloured pointer, at the speed its kit says (kits.kos), then
# reads the note back to check it. It asks you before each action. Answer y
# to each; hold Escape to stop it mid-action.
#   .\demo.ps1 -Agent coder-1
param([string]$Agent = "coder-1")
$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$env:PYTHONIOENCODING = "utf-8"
$note = Join-Path $env:TEMP "kosika-hands-note.json"
[IO.File]::WriteAllText($note, ('{"n": {"agent": "' + $Agent + '", "text": "Hello Aryan! This is ' + $Agent + ', typing fast with its own coloured pointer."}}'))
Set-Location $PSScriptRoot
python -m kosika.cli run main.kos --workflow note --input $note --allow-shell --allow-folder . --approve --approved-by Aryan
