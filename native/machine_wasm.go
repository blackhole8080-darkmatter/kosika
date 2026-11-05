//go:build wasm

package main

import "runtime"

// thisComputer, in a browser or another WebAssembly host: there is no
// system to ask, so only what Go itself knows is told.
func thisComputer() computerReading {
	return computerReading{cores: runtime.NumCPU(), name: "webassembly", system: runtime.GOOS + "/" + runtime.GOARCH, memory: "unknown", uptime: "unknown", disk: "unknown"}
}
