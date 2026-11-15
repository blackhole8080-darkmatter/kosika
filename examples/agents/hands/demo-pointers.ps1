# Two Kosika agents, each with its own pointer, pressing things in Notepad
# while your mouse stays where you leave it. Run it yourself; hold Escape to stop.
Set-Location $PSScriptRoot
# Colours as in kits.kos: coder-1 pink, coder-2 sky.
$one = @("--agent", "coder-1", "--colour", "#ff3ea5", "--speed", "fast")
$two = @("--agent", "coder-2", "--colour", "#55d6ff", "--speed", "steady")
python hands.py open notepad
Start-Sleep 1
python hands.py @one press notepad View
Start-Sleep 2
python hands.py key escape
python hands.py @two press notepad Edit
Start-Sleep 2
python hands.py key escape
python hands.py @one write notepad "coder-1 typing fast: the quick brown fox jumps over the lazy dog."
python hands.py @one read notepad
"Done. Your mouse did not move; the two pointers were the agents'."
