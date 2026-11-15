# Three Kosika coders, each writing one part of a plan to grow Kosika, at the
# same time, through the model router on port 8794 (free cloud models and
# Kosika's own). Each run's trace becomes a story in Kosika Studio.
$host.UI.RawUI.WindowTitle = "Kosika coders writing the plan"
$env:PYTHONIOENCODING = "utf-8"
$env:KOSIKA_ROUTER_PORT = "8794"
$env:KOSIKA_SWARM = "1"
Set-Location "C:\Users\Aryan\Aryan_Private\projects\kosika"
$py = "C:\Users\Aryan\Aryan_Private\kosika-daily\training\.venv\Scripts\python.exe"
$parts = @(
  @("coder-1", "write a new file PLAN_ENGINES.md: a plan to grow Kosika's language and its two engines (Python in kosika/, Go in native/) over the next month. Read KOSIKA.md first. Give ten concrete steps in order, each with why it matters, the files it touches, and how to tell it is done. Plain Markdown, headings and numbered steps, no code."),
  @("coder-2", "write a new file PLAN_MODELS.md: a plan to make Kosika's own models (kosika-coder, kosika-small, the models written in Kosika with std/learn.kos and std/layers.kos) and its virtual-model router smart and independent of cloud models, so the Kosika coder agents run on them. Read KOSIKA.md first. Give ten concrete steps in order, each with why it matters, the files it touches, and how to tell it is done. Plain Markdown, headings and numbered steps, no code."),
  @("coder-3", "write a new file PLAN_AGENTS.md: a plan to grow Kosika's agents and Studio: the coder agent and its swarm, the hands that see and act on the screen with their own pointers, the browser hands, permissions, learned routines and the Kosika logs. Read KOSIKA.md first. Give ten concrete steps in order, each with why it matters, the files it touches, and how to tell it is done. Plain Markdown, headings and numbered steps, no code.")
)
$jobs = foreach ($p in $parts) {
  $env:KOSIKA_AGENT = $p[0]
  Start-Process -FilePath $py -ArgumentList @("examples\agents\coder\daily\daily.py", "--take", "`"$($p[1])`"") -NoNewWindow -PassThru
}
$jobs | Wait-Process
"The three coders have written their parts. You can close this window."
