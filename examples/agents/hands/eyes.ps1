# The hands' eyes: what is in a window, by name, from Windows' own
# accessibility tree (UI Automation). No picture and no model: every button,
# menu, field and link the window tells Windows about, with where its middle
# is on the screen in real pixels.
#
#   powershell -File eyes.ps1 -Class Notepad            # one JSON line per thing
#   powershell -File eyes.ps1 -Class Notepad -Most 200
#
# It only reads, unless asked to -Press a thing by its name: then it presses
# that one thing through Windows' accessibility (invoke, expand, select or
# toggle, whichever it offers), without moving the mouse.
#
#   powershell -File eyes.ps1 -Class Notepad -Press "View"
#
# Or asked to -Read: the words in the window's first text field (Document or
# Edit), so what was typed can be checked against what is really there.
#
#   powershell -File eyes.ps1 -Class Notepad -Read
#
# Or asked to -Write: the words in the environment variable KOSIKA_HANDS_TEXT
# are added at the end of that text field through accessibility (its Value),
# with no keyboard and without bringing the window forward; -Clear empties
# it first. A field that cannot be written so says {"error": "...", "way":
# "field"}, exits 7, and nothing is changed.
#
#   powershell -File eyes.ps1 -Class Notepad -Write [-Clear]
param(
    [Parameter(Mandatory = $true)][string]$Class,
    [int]$Most = 150,
    [string]$Press = "",
    [switch]$Read,
    [switch]$Write,
    [switch]$Clear
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

if ($Read) {
    $isText = New-Object System.Windows.Automation.OrCondition(
        (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)),
        (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)))
    $field = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $isText)
    if ($null -eq $field) {
        [Console]::Out.WriteLine((@{ error = "no text field in the window" } | ConvertTo-Json -Compress))
        exit 2
    }
    $pattern = $null
    $text = $null
    if ($field.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) { $text = $pattern.DocumentRange.GetText(-1) }
    elseif ($field.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { $text = $pattern.Current.Value }
    if ($null -eq $text) {
        [Console]::Out.WriteLine((@{ error = "the text field cannot be read" } | ConvertTo-Json -Compress))
        exit 2
    }
    [Console]::Out.WriteLine((@{ text = $text } | ConvertTo-Json -Compress))
    exit 0
}

if ($Write -or $Clear) {
    $isText = New-Object System.Windows.Automation.OrCondition(
        (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)),
        (New-Object System.Windows.Automation.PropertyCondition($A::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)))
    $field = $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $isText)
    $pattern = $null
    if ($null -eq $field -or -not $field.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern) -or $pattern.Current.IsReadOnly) {
        [Console]::Out.WriteLine((@{ error = "this window's text field cannot be written directly"; way = "field" } | ConvertTo-Json -Compress))
        exit 7
    }
    $before = if ($Clear) { "" } else { $pattern.Current.Value }
    $words = if ($Write) { [string]$env:KOSIKA_HANDS_TEXT } else { "" }
    try {
        $pattern.SetValue($before + $words)
    } catch {
        [Console]::Out.WriteLine((@{ error = "the text field refused the words: $($_.Exception.Message)"; way = "field" } | ConvertTo-Json -Compress))
        exit 7
    }
    [Console]::Out.WriteLine(([ordered]@{ wrote = $words.Length; cleared = [bool]$Clear } | ConvertTo-Json -Compress))
    exit 0
}

if ($Press) {
    $all = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $found = $null
    foreach ($e in $all) { if ($e.Current.Name -and $e.Current.Name -ieq $Press -and -not $e.Current.IsOffscreen) { $found = $e; break } }
    if ($null -eq $found) {
        foreach ($e in $all) { if ($e.Current.Name -and $e.Current.Name.StartsWith($Press, [StringComparison]::OrdinalIgnoreCase) -and -not $e.Current.IsOffscreen) { $found = $e; break } }
    }
    if ($null -eq $found) {
        [Console]::Out.WriteLine((@{ error = "nothing called '$Press'" } | ConvertTo-Json -Compress))
        exit 2
    }
    $P = [System.Windows.Automation]
    $how = ""
    $pattern = $null
    if ($found.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { $pattern.Invoke(); $how = "invoked" }
    elseif ($found.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) { $pattern.Expand(); $how = "opened" }
    elseif ($found.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) { $pattern.Select(); $how = "selected" }
    elseif ($found.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) { $pattern.Toggle(); $how = "toggled" }
    else { $found.SetFocus(); $how = "focused" }
    $box = $found.Current.BoundingRectangle
    [Console]::Out.WriteLine(([ordered]@{ pressed = $found.Current.Name; how = $how; x = [int]($box.X + $box.Width / 2); y = [int]($box.Y + $box.Height / 2) } | ConvertTo-Json -Compress))
    exit 0
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
