# Two Kosika agents, each with its own pointer, pressing things in Notepad
# while your mouse stays where you leave it. Run it yourself; hold Escape to stop.
Set-Location $PSScriptRoot
python hands.py open notepad
Start-Sleep 1
$env:KOSIKA_AGENT = "coder-1"; python hands.py press notepad View
Start-Sleep 2
python hands.py key escape
$env:KOSIKA_AGENT = "coder-2"; python hands.py press notepad Edit
Start-Sleep 2
python hands.py key escape
$env:KOSIKA_AGENT = "coder-1"; python hands.py press notepad File
Start-Sleep 2
python hands.py key escape
"Done. Your mouse did not move; the two pointers were the agents'."
