package main

import (
	"fmt"
	"os"
	"runtime"
	"syscall"
	"unsafe"
)

var (
	kernel32 = syscall.NewLazyDLL("kernel32.dll")
	ntdll    = syscall.NewLazyDLL("ntdll.dll")
)

type memoryStatus struct {
	length, load                                     uint32
	total, free, pageTotal, pageFree                 uint64
	virtualTotal, virtualFree, virtualExtendedUnused uint64
}

type versionInfo struct {
	size, major, minor, build, platform uint32
	servicePack                         [128]uint16
}

// thisComputer is this computer as Windows tells it, as windows() in
// kosika/mayaos.py reads it.
func thisComputer() computerReading {
	found := computerReading{system: "Windows " + windowsRelease(), cores: runtime.NumCPU()}
	found.name, _ = os.Hostname()
	drive := os.Getenv("SYSTEMDRIVE")
	if drive == "" {
		drive = "C:"
	}
	found.disk = "unknown"
	if path, err := syscall.UTF16PtrFromString(drive + `\`); err == nil {
		var available, total, free uint64
		if ok, _, _ := kernel32.NewProc("GetDiskFreeSpaceExW").Call(uintptr(unsafe.Pointer(path)),
			uintptr(unsafe.Pointer(&available)), uintptr(unsafe.Pointer(&total)), uintptr(unsafe.Pointer(&free))); ok != 0 {
			found.disk = gigabytesUsed(total-free, total, drive)
		}
	}
	status := memoryStatus{length: uint32(unsafe.Sizeof(memoryStatus{}))}
	found.memory = "unknown"
	if ok, _, _ := kernel32.NewProc("GlobalMemoryStatusEx").Call(uintptr(unsafe.Pointer(&status))); ok != 0 && status.total != 0 {
		found.memory = fmt.Sprintf("%.0f of %.0f MB used", float64(status.total-status.free)/(1<<20), float64(status.total)/(1<<20))
	}
	ticks, _, _ := kernel32.NewProc("GetTickCount64").Call()
	seconds := uint64(ticks) / 1000
	minutes, hours := (seconds/60)%60, seconds/3600
	days := hours / 24
	hours %= 24
	found.uptime = fmt.Sprintf("%d h %d min", hours, minutes)
	if days > 0 {
		found.uptime = fmt.Sprintf("%d d ", days) + found.uptime
	}
	return found
}

// windowsRelease is the release Python's platform module names: 11 from
// build 22000 on, as it reads the version.
func windowsRelease() string {
	info := versionInfo{size: uint32(unsafe.Sizeof(versionInfo{}))}
	ntdll.NewProc("RtlGetVersion").Call(uintptr(unsafe.Pointer(&info)))
	switch {
	case info.major == 10 && info.build >= 22000:
		return "11"
	case info.major == 10:
		return "10"
	case info.major == 6 && info.minor == 3:
		return "8.1"
	case info.major == 6 && info.minor == 2:
		return "8"
	case info.major == 6 && info.minor == 1:
		return "7"
	}
	return fmt.Sprintf("%d.%d", info.major, info.minor)
}
