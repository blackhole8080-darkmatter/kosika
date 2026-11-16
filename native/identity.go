package main

// One program's identity, as kosika/identity.py computes it: a hash of the
// program's sources, not of either engine's tree. Both engines read the same
// module bytes and already digest them the same way, so both name a program
// the same way, and a run recorded by one can be matched to a pack signed by
// the other.

import (
	"crypto/sha256"
	"encoding/hex"
	"sort"
	"strings"
)

// identitySchema begins what is hashed. Raising it names every program
// differently, so it changes only with the format.
const identitySchema = "kosika-program/v1"

// identityOf is kosika.identity.identity: the schema line, then one line per
// module in sorted path order, each the path, a space and the module's digest.
// Each module is named by its path relative to the program's own folder, or
// by `std/NAME.kos` for the standard library.
func identityOf(moduleHashes map[string]string) string {
	paths := make([]string, 0, len(moduleHashes))
	for path := range moduleHashes {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	var written strings.Builder
	written.WriteString(identitySchema)
	for _, path := range paths {
		written.WriteString("\n")
		written.WriteString(path)
		written.WriteString(" ")
		written.WriteString(moduleHashes[path])
	}
	sum := sha256.Sum256([]byte(written.String()))
	return hex.EncodeToString(sum[:])
}
