//go:build !windows && !wasm

package main

import (
	"os"
	"os/exec"
	"runtime"
	"strings"
	"syscall"
)

// thisComputer is this computer as its system tells it. Memory and uptime
// are read only on Windows, as in the reference.
func thisComputer() computerReading {
	found := computerReading{cores: runtime.NumCPU(), memory: "unknown", uptime: "unknown", disk: "unknown"}
	found.name, _ = os.Hostname()
	if said, err := exec.Command("uname", "-sr").Output(); err == nil {
		found.system = strings.TrimSpace(string(said))
	}
	var disk syscall.Statfs_t
	if syscall.Statfs("/", &disk) == nil {
		size := uint64(disk.Bsize)
		found.disk = gigabytesUsed((uint64(disk.Blocks)-uint64(disk.Bfree))*size, uint64(disk.Blocks)*size, "/")
	}
	return found
}
