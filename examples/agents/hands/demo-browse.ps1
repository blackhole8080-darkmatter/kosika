# Live demo of the browsing hands: give them a goal in words; the model
# plans each next step after reading the page, and Kosika asks you before
# every step. Needs the model router on port 8791 (learning_router.py).
param([string]$Goal = "Open Kosika's page on GitHub, https://github.com/blackhole8080-darkmatter/kosika , and tell me in a few sentences what its README says Kosika is.")
$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$env:PYTHONIOENCODING = "utf-8"
$file = Join-Path $env:TEMP "kosika-hands-goal.json"
[IO.File]::WriteAllText($file, (@{ g = @{ text = $Goal } } | ConvertTo-Json -Compress))
Set-Location $PSScriptRoot
python -m kosika.cli run browse.kos --input $file --allow-shell --allow-folder . --approve --approved-by Aryan --model kosika-virtual --model-url http://127.0.0.1:8791/v1
