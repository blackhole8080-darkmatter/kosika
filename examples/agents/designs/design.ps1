# Kosika Designs: any prompt in, a clear drawing out.
#
#   .\design.ps1 "how does an LLM work"
#   .\design.ps1 "create a visual representation of the Fibonacci sequence" -Kosika path\to\kosika-native.exe
#
# 1. main.kos `describe`: a free Groq model describes the structure as data;
#    Kosika checks it and sends any problems back to the model once.
# 2. For a figure Kosika draws from its own maths (the golden spiral), eyes.py
#    finds a freely licensed real photo of it to show beside the drawing.
# 3. draw.kos lays it out and draws it, with the hover sentences.
#
# Needs GROQ_API_KEY as a user environment variable. Writes design.html.
# With -Model kosika-virtual -ModelUrl http://127.0.0.1:8791/v1 it asks through
# the virtual model (examples/virtual_models/router.py), which moves on to
# another free model when one is rate-limited or returns broken JSON.
param(
  [Parameter(Mandatory = $true)][string]$Prompt,
  [string]$Model = "openai/gpt-oss-120b",
  [string]$ModelUrl = "https://api.groq.com/openai/v1",
  [string]$Out = "design.html",
  [string]$Kosika = "kosika"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$work = Join-Path ([IO.Path]::GetTempPath()) "kosika-designs"
New-Item -ItemType Directory -Force $work | Out-Null
$env:KOSIKA_MODEL_API_KEY = [Environment]::GetEnvironmentVariable("GROQ_API_KEY", "User")

# kosika run prints the result record, then a line naming its trace.
function Read-Result($text) {
  $record = ($text -split "`nTrace:")[0]
  if (-not $record.TrimStart().StartsWith("{")) { throw $record.Trim() }
  return $record | python -c "import ast, json, sys; print(json.dumps(ast.literal_eval(sys.stdin.read())))" | ConvertFrom-Json
}

@{ ask = @{ text = $Prompt } } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $work "ask.json")
$said = & $Kosika run (Join-Path $here "main.kos") --workflow describe --input (Join-Path $work "ask.json") `
  --model $Model --model-url $ModelUrl --model-protocol openai --model-timeout 400 --max-output-tokens 8000 | Out-String
$request = Read-Result $said
Write-Host "Described: $($request.structure.title) ($($request.structure.shape), $($request.structure.parts.Count) parts)"

if ($request.structure.figure -eq "golden_spiral") {
  @{ search = "sunflower head close-up florets"; needs = @("sunflower", "helianthus") } | ConvertTo-Json |
    Set-Content -Encoding utf8 (Join-Path $work "photo-plan.json")
  python (Join-Path $here "eyes.py") (Join-Path $work "photo-plan.json") (Join-Path $work "picture.json") --only-photo
  $request.picture = Get-Content (Join-Path $work "picture.json") -Raw | ConvertFrom-Json
}

@{ r = $request } | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 (Join-Path $work "request.json")
$drawn = Read-Result (& $Kosika run (Join-Path $here "draw.kos") --workflow draw --input (Join-Path $work "request.json") | Out-String)
if ($drawn.problems.Count -gt 0) { throw "Kosika would not draw it: $($drawn.problems -join '; ')" }
[IO.File]::WriteAllText((Join-Path (Get-Location) $Out), $drawn.page)
Write-Host "Wrote $Out"
