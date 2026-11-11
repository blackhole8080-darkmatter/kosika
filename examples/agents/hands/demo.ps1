# Live demo of the hands agent: Kosika opens Notepad and types a note,
# asking you before each of its four actions. Answer y to each; hold
# Escape to stop it mid-action.
$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$env:PYTHONIOENCODING = "utf-8"
$note = Join-Path $env:TEMP "kosika-hands-note.json"
[IO.File]::WriteAllText($note, '{"n": {"text": "Hello Aryan! This is Kosika, typing with its own cursor."}}')
Set-Location $PSScriptRoot
python -m kosika.cli run main.kos --workflow note --input $note --allow-shell --allow-folder . --approve --approved-by Aryan
