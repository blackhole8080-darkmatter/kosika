//go:build payload

package main

import _ "embed"

// payload is the whole app, zipped by desktop/build.py.
//
//go:embed payload.zip
var payload []byte
