package main

// The tools that look at the machine, as kosika/wsl.py and kosika/mayaos.py
// have them: a `system "linux"` tool reads how the one Linux under WSL the
// person named (--linux NAME) is doing, by commands fixed here that change
// nothing; a `system "computer"` tool reads how this computer is doing,
// when the person allows it (--computer).

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
	"unicode"
	"unicode/utf16"
)

const wslSeconds = 60

// wslStatusScript is what a linux tool asks, in one go, as plain lines
// `key=value`: the reference's STATUS_SCRIPT, word for word.
const wslStatusScript = `
echo "kernel=$(uname -sr)"
echo "uptime=$(uptime -p 2>/dev/null || uptime)"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
free -m | awk '/^Mem:/ {print "memory=" $3 " of " $2 " MB used"}'
df -h / | awk 'NR==2 {print "disk=" $3 " of " $2 " used (" $5 ") on /"}'
if command -v apt >/dev/null 2>&1; then
  echo "upgrades=$(apt list --upgradable 2>/dev/null | grep -c upgradable)"
else
  echo "upgrades=unknown"
fi
ps -eo comm,%cpu --sort=-%cpu | awk 'NR>1 && NR<=6 {printf "top=%s %s%%\n", $1, $2}'
`

var linuxFields = []string{"kernel", "uptime", "load", "memory", "disk", "upgrades"}

// plainDistro is the distribution named with --linux, or why it is refused.
func plainDistro(distro string) (string, string) {
	name := strings.TrimSpace(distro)
	plain := name != ""
	for _, letter := range name {
		if !(unicode.IsLetter(letter) || unicode.IsDigit(letter) || strings.ContainsRune("-_.", letter)) {
			plain = false
		}
	}
	if !plain {
		return "", fmt.Sprintf("Linux distribution %s is not a plain name", pyRepr(distro))
	}
	return name, ""
}

// wslText is what wsl.exe printed: wsl.exe itself speaks UTF-16, what
// Linux prints is UTF-8.
func wslText(raw []byte) string {
	if len(raw) > 1 && (raw[0] == 0xff && raw[1] == 0xfe || raw[0] == 0xfe && raw[1] == 0xff || raw[1] == 0) {
		units := make([]uint16, len(raw)/2)
		for i := range units {
			units[i] = uint16(raw[2*i]) | uint16(raw[2*i+1])<<8
		}
		return strings.ReplaceAll(string(utf16.Decode(units)), "\x00", "")
	}
	return strings.ToValidUTF8(string(raw), "�")
}

// linuxTool is a `system "linux"` tool's `status`.
func (r *run) linuxTool() Value {
	ctx, cancel := context.WithTimeout(context.Background(), wslSeconds*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, "wsl.exe", "-d", r.linux, "--exec", "sh", "-c", wslStatusScript)
	var out, err strings.Builder
	stdout, stderr := &byteSink{}, &byteSink{}
	command.Stdout, command.Stderr = stdout, stderr
	failure := command.Run()
	if ctx.Err() == context.DeadlineExceeded {
		runFail(fmt.Sprintf("Linux did not answer within %d seconds", wslSeconds))
	}
	var missing *exec.Error
	if errors.As(failure, &missing) {
		runFail("WSL is not installed on this computer")
	}
	out.WriteString(wslText(stdout.data))
	err.WriteString(wslText(stderr.data))
	if failure != nil {
		said := err.String()
		if said == "" {
			said = out.String()
		}
		runFail(fmt.Sprintf("Linux (%s) did not answer: %s", r.linux, clipText(pyStrip(said), 300)))
	}
	found := map[string]string{}
	var top []string
	for _, line := range strings.Split(universal(out.String()), "\n") {
		key, value, _ := strings.Cut(line, "=")
		if key == "top" {
			top = append(top, pyStrip(value))
		} else if hasName(linuxFields, key) {
			found[key] = pyStrip(value)
		}
	}
	result := newRecord()
	result.set("distro", r.linux)
	for _, key := range linuxFields {
		value, ok := found[key]
		if !ok {
			value = "unknown"
		}
		result.set(key, value)
	}
	result.set("top", stringValues(top))
	return result
}

type byteSink struct{ data []byte }

func (b *byteSink) Write(p []byte) (int, error) {
	b.data = append(b.data, p...)
	return len(p), nil
}

// computerTool is a `system "computer"` tool's `status`: how this computer
// is doing, as its system tells it.
func computerTool() Value {
	found := thisComputer()
	result := newRecord()
	result.set("name", found.name)
	result.set("system", found.system)
	result.set("cores", bigInt(found.cores))
	result.set("memory", found.memory)
	result.set("disk", found.disk)
	result.set("uptime", found.uptime)
	return result
}

type computerReading struct {
	name, system, memory, disk, uptime string
	cores                              int
}

// gigabytesUsed is a disk as the reference writes it.
func gigabytesUsed(used, total uint64, drive string) string {
	return fmt.Sprintf("%.0f of %.0f GB used on %s", float64(used)/(1<<30), float64(total)/(1<<30), drive)
}
