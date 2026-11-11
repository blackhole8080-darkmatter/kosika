# The hands' eyes: what is in a window, by name, from Windows' own
# accessibility tree (UI Automation). No picture and no model: every button,
# menu, field and link the window tells Windows about, with where its middle
# is on the screen in real pixels.
#
#   powershell -File eyes.ps1 -Class Notepad            # one JSON line per thing
#   powershell -File eyes.ps1 -Class Notepad -Most 200
#
# It only reads; it never clicks or types.
param(
    [Parameter(Mandatory = $true)][string]$Class,
    [int]$Most = 150
)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Dpi { [DllImport("user32.dll")] public static extern IntPtr SetProcessDpiAwarenessContext(IntPtr v); }
"@
[void][Dpi]::SetProcessDpiAwarenessContext([IntPtr](-4))

$A = [System.Windows.Automation.AutomationElement]
$root = $A::RootElement
$byClass = New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, $Class)
$window = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $byClass)
if ($null -eq $window) {
    [Console]::Out.WriteLine((@{ error = "no window of class $Class" } | ConvertTo-Json -Compress))
    exit 2
}

$seen = 0
$all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($e in $all) {
    if ($seen -ge $Most) { break }
    $info = $e.Current
    if ($info.IsOffscreen) { continue }
    $box = $info.BoundingRectangle
    if ($box.IsEmpty -or $box.Width -lt 1) { continue }
    $kind = $info.ControlType.ProgrammaticName -replace '^ControlType\.', ''
    if (-not $info.Name -and $kind -notin @('Edit', 'Document')) { continue }
    $line = [ordered]@{
        name = $info.Name
        kind = $kind
        x    = [int]($box.X + $box.Width / 2)
        y    = [int]($box.Y + $box.Height / 2)
    }
    [Console]::Out.WriteLine(($line | ConvertTo-Json -Compress))
    $seen++
}
