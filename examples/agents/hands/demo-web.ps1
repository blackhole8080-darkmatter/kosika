# Live demo of the browser hands: Kosika opens its own Chrome (its own
# profile, none of your sign-ins), goes to a website and reads it back as
# text with every link, button and field numbered, asking before each action.
param([string]$Url = "https://en.wikipedia.org/wiki/Special:Random", [string]$Agent = "scout")
$env:PYTHONPATH = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$env:PYTHONIOENCODING = "utf-8"
$site = Join-Path $env:TEMP "kosika-hands-site.json"
[IO.File]::WriteAllText($site, '{"s": {"agent": "' + $Agent + '", "url": "' + $Url + '"}}')
Set-Location $PSScriptRoot
python -m kosika.cli run main.kos --workflow visit --input $site --allow-shell --allow-folder . --approve --approved-by Aryan
