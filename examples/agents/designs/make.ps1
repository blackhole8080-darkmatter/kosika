# Kosika Designs, every step in order: a prompt in, a page out.
#
#   .\make.ps1 "create a visual representation of the Fibonacci sequence"
#
# 1. main.kos thinks of the best real-world example (a free Groq model).
# 2. eyes.py finds freely licensed photos of it on Wikimedia Commons and
#    reads where the florets are; a free vision model makes a first guess.
# 3. sunflower.kos measures, checks and draws. If the photo fails Kosika's
#    check, the next photo is tried, up to four; the first is kept as an
#    illustration if none passes.
#
# Needs GROQ_API_KEY as a user environment variable. Writes design.html.
#
# -Kosika picks the engine: the kosika command (Python, about 2 minutes a
# photo) or kosika-native.exe (Go, about 2 seconds a photo, same result).
param(
  [Parameter(Mandatory = $true)][string]$Prompt,
  [string]$Model = "openai/gpt-oss-120b",
  [string]$Vision = "qwen/qwen3.8-27b",
  [string]$Out = "design.html",
  [string]$Kosika = "kosika"
)
$ErrorActionPreference = "Stop"
$kept = $null
$here = $PSScriptRoot
$work = Join-Path ([IO.Path]::GetTempPath()) "kosika-designs"
New-Item -ItemType Directory -Force $work | Out-Null
$key = [Environment]::GetEnvironmentVariable("GROQ_API_KEY", "User")
$env:GROQ_API_KEY = $key
$env:KOSIKA_MODEL_API_KEY = $key

# kosika run prints the result record, then a line naming its trace.
function Read-Result($text) {
  $record = ($text -split "`nTrace:")[0]
  return $record | python -c "import ast, json, sys; print(json.dumps(ast.literal_eval(sys.stdin.read())))" | ConvertFrom-Json
}

@{ ask = @{ text = $Prompt } } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $work "ask.json")
$said = & $Kosika run (Join-Path $here "main.kos") --workflow think --input (Join-Path $work "ask.json") `
  --model $Model --model-url https://api.groq.com/openai/v1 --model-protocol openai --trace (Join-Path $work "think.jsonl") | Out-String
$plan = Read-Result $said
$plan | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $work "plan.json")
Write-Host "Example: $($plan.example) (others: $($plan.others -join ', '))"

for ($pick = 0; $pick -lt 4; $pick++) {
  python (Join-Path $here "eyes.py") (Join-Path $work "plan.json") (Join-Path $work "measured.json") --model $Vision --pick $pick
  # The Python engine needs its call budget raised for this much maths;
  # the Go engine takes no such flags.
  $budget = if ($Kosika -like "*native*") { @() } else { @("--max-function-calls", "1000000", "--max-workflow-steps", "100000") }
  $made = & $Kosika run (Join-Path $here "sunflower.kos") --workflow design --input (Join-Path $work "measured.json") `
    @budget --trace (Join-Path $work "design.jsonl") | Out-String
  if (-not $made.TrimStart().StartsWith("{")) { Write-Host $made.Trim(); continue }
  $design = Read-Result $made
  # Keep the first photo drawn, the model's best, unless a later one is measured.
  if (-not $kept -or $design.verdict.StartsWith("Measured")) { $kept = $design }
  Write-Host $design.verdict
  if ($design.verdict.StartsWith("Measured")) { break }
}
if (-not $kept) { throw "No photo could be drawn." }
[IO.File]::WriteAllText((Join-Path (Get-Location) $Out), $kept.page)
Write-Host "Wrote $Out"
