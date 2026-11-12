// Phones as Kosika nodes.
//
// The same kosika-native program runs on the laptop and, built for Android,
// on the phone in Termux. The phone runs `phone node`; the laptop pairs with
// it once, with a one-time code the phone shows, and then asks it for a sense
// (`phone ask`) or to do something (`phone do`). The phone asks its person
// yes or no before each one. Every message is a signed, encrypted log line,
// and both devices keep a hash-chained copy, so who asked for what can always
// be read and checked: `phone log NAME --check`.
//
// Nothing here needs a package outside Go's own: X25519 to agree a key,
// Ed25519 to sign, AES-GCM to seal, SHA-256 to chain.
package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const phoneVersion = 1
const phoneDefaultPort = 8794

var b64 = base64.StdEncoding

// phoneHome is where this device keeps its key, its peers and its logs.
func phoneHome() string {
	if home := os.Getenv("KOSIKA_PHONE_HOME"); home != "" {
		return home
	}
	user, err := os.UserHomeDir()
	if err != nil {
		user = "."
	}
	return filepath.Join(user, ".kosika", "phone")
}

// ---- this device ----

type phoneIdentity struct {
	Name string `json:"name"`
	Seed string `json:"seed"` // Ed25519 seed, base64; never leaves the device
}

func (id phoneIdentity) private() ed25519.PrivateKey {
	seed, _ := b64.DecodeString(id.Seed)
	return ed25519.NewKeyFromSeed(seed)
}

func (id phoneIdentity) public() ed25519.PublicKey {
	return id.private().Public().(ed25519.PublicKey)
}

// deviceID names a device by its public key: the first 16 hex digits of
// its hash, short enough to read aloud and compare.
func deviceID(public []byte) string {
	sum := sha256.Sum256(public)
	return hex.EncodeToString(sum[:8])
}

func loadIdentity(name string) (phoneIdentity, error) {
	path := filepath.Join(phoneHome(), "identity.json")
	var id phoneIdentity
	if data, err := os.ReadFile(path); err == nil {
		if err := json.Unmarshal(data, &id); err != nil {
			return id, fmt.Errorf("cannot read %s: %v", path, err)
		}
		if name != "" && name != id.Name {
			id.Name = name
			return id, writePrivate(path, id)
		}
		return id, nil
	}
	seed := make([]byte, ed25519.SeedSize)
	if _, err := rand.Read(seed); err != nil {
		return id, err
	}
	if name == "" {
		name, _ = os.Hostname()
		if name == "" {
			name = "kosika"
		}
	}
	id = phoneIdentity{Name: cleanName(name), Seed: b64.EncodeToString(seed)}
	return id, writePrivate(path, id)
}

var nameShape = regexp.MustCompile(`[^a-zA-Z0-9_-]+`)

func cleanName(name string) string {
	name = strings.Trim(nameShape.ReplaceAllString(strings.ToLower(name), "-"), "-")
	if name == "" {
		return "device"
	}
	if len(name) > 32 {
		name = name[:32]
	}
	return name
}

// ---- peers ----

// phonePeer is a device this one has paired with. Role says what the other
// side is: a "node" this device asks, or a "hub" that asks this device.
type phonePeer struct {
	Name     string   `json:"name"`
	Role     string   `json:"role"`
	Address  string   `json:"address,omitempty"`
	Public   string   `json:"public"` // its Ed25519 key, base64
	Key      string   `json:"key"`    // the shared AES key, base64
	PairedAt string   `json:"paired_at"`
	SeqOut   uint64   `json:"seq_out"`
	SeqIn    uint64   `json:"seq_in"`
	Grants   []string `json:"grants,omitempty"` // senses given ahead of time, as "ask:battery"
}

func (p phonePeer) public() ed25519.PublicKey {
	key, _ := b64.DecodeString(p.Public)
	return ed25519.PublicKey(key)
}

func (p phonePeer) key() []byte {
	key, _ := b64.DecodeString(p.Key)
	return key
}

func (p phonePeer) granted(what string) bool {
	for _, grant := range p.Grants {
		if grant == what {
			return true
		}
	}
	return false
}

func peerPath(name string) string {
	return filepath.Join(phoneHome(), "peers", cleanName(name)+".json")
}

func loadPeer(name string) (phonePeer, error) {
	var peer phonePeer
	data, err := os.ReadFile(peerPath(name))
	if err != nil {
		return peer, fmt.Errorf("no paired device called %q (see `kosika-native phone list`)", name)
	}
	return peer, json.Unmarshal(data, &peer)
}

func savePeer(peer phonePeer) error {
	return writePrivate(peerPath(peer.Name), peer)
}

func listPeers() []phonePeer {
	paths, _ := filepath.Glob(filepath.Join(phoneHome(), "peers", "*.json"))
	sort.Strings(paths)
	peers := []phonePeer{}
	for _, path := range paths {
		var peer phonePeer
		if data, err := os.ReadFile(path); err == nil && json.Unmarshal(data, &peer) == nil {
			peers = append(peers, peer)
		}
	}
	return peers
}

// peerByPublic finds the paired device that holds this key.
func peerByPublic(public string) (phonePeer, bool) {
	for _, peer := range listPeers() {
		if peer.Public == public {
			return peer, true
		}
	}
	return phonePeer{}, false
}

func writePrivate(path string, value any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

// ---- pairing ----

// pairHello is what each side says first: who it is, its lasting signing
// key, a key made for this pairing only, and a fresh nonce.
type pairHello struct {
	Name   string `json:"name"`
	Public string `json:"public"`
	Share  string `json:"share"` // X25519 public key, base64
	Nonce  string `json:"nonce"`
}

func newShare() (*ecdh.PrivateKey, error) {
	return ecdh.X25519().GenerateKey(rand.Reader)
}

func randomText(size int) string {
	bytes := make([]byte, size)
	_, _ = rand.Read(bytes)
	return b64.EncodeToString(bytes)
}

// transcriptHash binds the key to everything both sides said, in order.
func transcriptHash(hub, node pairHello) []byte {
	sum := sha256.New()
	for _, part := range []string{"kosika-phone-pair-v1", hub.Name, hub.Public, hub.Share, hub.Nonce, node.Name, node.Public, node.Share, node.Nonce} {
		fmt.Fprintf(sum, "%d:%s|", len(part), part)
	}
	return sum.Sum(nil)
}

// pairKey turns the agreed secret and the one-time code into the shared
// key. Someone who only listened on the Wi-Fi cannot make it (they lack the
// X25519 secret); someone in the middle cannot either (they lack the code).
func pairKey(mine *ecdh.PrivateKey, theirShare string, code string, transcript []byte) ([]byte, error) {
	raw, err := b64.DecodeString(theirShare)
	if err != nil {
		return nil, errors.New("bad pairing key")
	}
	theirs, err := ecdh.X25519().NewPublicKey(raw)
	if err != nil {
		return nil, errors.New("bad pairing key")
	}
	secret, err := mine.ECDH(theirs)
	if err != nil {
		return nil, err
	}
	return hkdf(secret, []byte(normalCode(code)), transcript, 32), nil
}

// hkdf is RFC 5869 HKDF with SHA-256.
func hkdf(secret, salt, info []byte, size int) []byte {
	extract := hmac.New(sha256.New, salt)
	extract.Write(secret)
	prk := extract.Sum(nil)
	out, block := []byte{}, []byte{}
	for counter := byte(1); len(out) < size; counter++ {
		expand := hmac.New(sha256.New, prk)
		expand.Write(block)
		expand.Write(info)
		expand.Write([]byte{counter})
		block = expand.Sum(nil)
		out = append(out, block...)
	}
	return out[:size]
}

func pairProof(key []byte, side string, transcript []byte) string {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(side))
	mac.Write(transcript)
	return b64.EncodeToString(mac.Sum(nil))
}

// The code's letters leave out ones easily mistaken for others (0/O, 1/I/L).
const codeLetters = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

func newPairCode() string {
	raw := make([]byte, 8)
	_, _ = rand.Read(raw)
	code := make([]byte, 0, 9)
	for i, b := range raw {
		if i == 4 {
			code = append(code, '-')
		}
		code = append(code, codeLetters[int(b)%len(codeLetters)])
	}
	return string(code)
}

func normalCode(code string) string {
	return strings.ReplaceAll(strings.ToUpper(strings.TrimSpace(code)), "-", "")
}

// fingerprint is a short check both screens show after pairing: the same
// six groups on both means no one sat in the middle.
func fingerprint(a, b string) string {
	keys := []string{a, b}
	sort.Strings(keys)
	sum := sha256.Sum256([]byte(keys[0] + "|" + keys[1]))
	text := strings.ToUpper(hex.EncodeToString(sum[:6]))
	return text[0:4] + " " + text[4:8] + " " + text[8:12]
}

// ---- messages ----

// phoneBody is what a message says, sealed inside its envelope.
type phoneBody struct {
	Kind      string            `json:"kind"` // ping, ask, do, reply
	What      string            `json:"what,omitempty"`
	Action    string            `json:"action,omitempty"`
	Args      map[string]string `json:"args,omitempty"`
	Re        uint64            `json:"re,omitempty"`     // the request a reply answers
	Status    string            `json:"status,omitempty"` // done, refused, failed
	Result    any               `json:"result,omitempty"`
	Note      string            `json:"note,omitempty"`
	Simulated bool              `json:"simulated,omitempty"`
}

// phoneEnvelope is one message on the wire: who sent it to whom, its place
// in the sender's sequence, the sealed body, and the sender's signature
// over all of it.
type phoneEnvelope struct {
	V     int    `json:"v"`
	From  string `json:"from"`
	To    string `json:"to"`
	Seq   uint64 `json:"seq"`
	At    string `json:"at"`
	Nonce string `json:"nonce"`
	Box   string `json:"box"`
	Sig   string `json:"sig,omitempty"`
}

func (e phoneEnvelope) header() []byte {
	return []byte(fmt.Sprintf("kosika-phone-v%d|%s|%s|%d|%s", e.V, e.From, e.To, e.Seq, e.At))
}

func (e phoneEnvelope) signed() []byte {
	e.Sig = ""
	data, _ := json.Marshal(e)
	return data
}

// seal makes the envelope for a body: sealed with the shared key, signed
// with the sender's own.
func seal(me phoneIdentity, peer phonePeer, seq uint64, body phoneBody) (phoneEnvelope, error) {
	envelope := phoneEnvelope{
		V: phoneVersion, From: deviceID(me.public()), To: deviceID(peer.public()),
		Seq: seq, At: time.Now().UTC().Format(time.RFC3339),
	}
	block, err := aes.NewCipher(peer.key())
	if err != nil {
		return envelope, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return envelope, err
	}
	nonce := make([]byte, gcm.NonceSize())
	_, _ = rand.Read(nonce)
	plain, _ := json.Marshal(body)
	envelope.Nonce = b64.EncodeToString(nonce)
	envelope.Box = b64.EncodeToString(gcm.Seal(nil, nonce, plain, envelope.header()))
	envelope.Sig = b64.EncodeToString(ed25519.Sign(me.private(), envelope.signed()))
	return envelope, nil
}

// open checks an envelope came from the peer, unchanged, meant for this
// device, and gives its body.
func open(me phoneIdentity, peer phonePeer, envelope phoneEnvelope) (phoneBody, error) {
	var body phoneBody
	if envelope.V != phoneVersion {
		return body, fmt.Errorf("message version %d is not %d", envelope.V, phoneVersion)
	}
	if envelope.From != deviceID(peer.public()) || envelope.To != deviceID(me.public()) {
		return body, errors.New("message is not from that device to this one")
	}
	sig, err := b64.DecodeString(envelope.Sig)
	if err != nil || !ed25519.Verify(peer.public(), envelope.signed(), sig) {
		return body, errors.New("signature does not match")
	}
	block, err := aes.NewCipher(peer.key())
	if err != nil {
		return body, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return body, err
	}
	nonce, err1 := b64.DecodeString(envelope.Nonce)
	box, err2 := b64.DecodeString(envelope.Box)
	if err1 != nil || err2 != nil || len(nonce) != gcm.NonceSize() {
		return body, errors.New("message is malformed")
	}
	plain, err := gcm.Open(nil, nonce, box, envelope.header())
	if err != nil {
		return body, errors.New("message does not open with the shared key")
	}
	decoder := json.NewDecoder(bytes.NewReader(plain))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		return body, fmt.Errorf("message body is malformed: %v", err)
	}
	return body, nil
}

// ---- the log ----

// phoneLogLine is one line of a device's log with a peer: the message as it
// travelled, what it said, and the hash chain that keeps the log whole.
type phoneLogLine struct {
	At       string         `json:"at"`
	Dir      string         `json:"dir"` // out or in
	Peer     string         `json:"peer"`
	Envelope *phoneEnvelope `json:"envelope,omitempty"`
	Body     phoneBody      `json:"body"`
	Prev     string         `json:"prev"`
	Hash     string         `json:"hash"`
}

func logPath(peer string) string {
	return filepath.Join(phoneHome(), "logs", cleanName(peer)+".jsonl")
}

func lineHash(line phoneLogLine) string {
	line.Hash = ""
	data, _ := json.Marshal(line)
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func readLog(peer string) ([]phoneLogLine, error) {
	data, err := os.ReadFile(logPath(peer))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	lines := []phoneLogLine{}
	for number, text := range strings.Split(strings.TrimRight(string(data), "\n"), "\n") {
		if text == "" {
			continue
		}
		var line phoneLogLine
		if err := json.Unmarshal([]byte(text), &line); err != nil {
			return lines, fmt.Errorf("line %d is not a log line: %v", number+1, err)
		}
		lines = append(lines, line)
	}
	return lines, nil
}

func appendLog(peer string, dir string, envelope *phoneEnvelope, body phoneBody) error {
	lines, err := readLog(peer)
	if err != nil {
		return err
	}
	prev := "start"
	if len(lines) > 0 {
		prev = lines[len(lines)-1].Hash
	}
	line := phoneLogLine{At: time.Now().UTC().Format(time.RFC3339), Dir: dir, Peer: peer, Envelope: envelope, Body: body, Prev: prev}
	line.Hash = lineHash(line)
	if err := os.MkdirAll(filepath.Dir(logPath(peer)), 0o700); err != nil {
		return err
	}
	file, err := os.OpenFile(logPath(peer), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	data, _ := json.Marshal(line)
	_, err = file.Write(append(data, '\n'))
	return err
}

// checkLog proves the log is whole: each line's hash, the chain from the
// first, and for messages, that the signature is the right device's and
// the sealed body says what the line says.
func checkLog(me phoneIdentity, peer phonePeer, lines []phoneLogLine) error {
	prev := "start"
	for number, line := range lines {
		if line.Prev != prev {
			return fmt.Errorf("line %d does not follow line %d", number+1, number)
		}
		if lineHash(line) != line.Hash {
			return fmt.Errorf("line %d was changed after it was written", number+1)
		}
		prev = line.Hash
		if line.Envelope == nil {
			continue
		}
		sender, receiver := me, peer
		if line.Dir == "in" {
			// open() checks a message sent to me, so swap roles for "out".
			body, err := open(me, peer, *line.Envelope)
			if err != nil {
				return fmt.Errorf("line %d: %v", number+1, err)
			}
			if !sameBody(body, line.Body) {
				return fmt.Errorf("line %d says something its message does not", number+1)
			}
			continue
		}
		sig, err := b64.DecodeString(line.Envelope.Sig)
		if err != nil || !ed25519.Verify(sender.public(), line.Envelope.signed(), sig) {
			return fmt.Errorf("line %d: this device's signature does not match", number+1)
		}
		if line.Envelope.To != deviceID(receiver.public()) {
			return fmt.Errorf("line %d was not sent to %s", number+1, peer.Name)
		}
		body, err := openOwn(peer, *line.Envelope)
		if err != nil {
			return fmt.Errorf("line %d: %v", number+1, err)
		}
		if !sameBody(body, line.Body) {
			return fmt.Errorf("line %d says something its message does not", number+1)
		}
	}
	return nil
}

// openOwn opens a message this device sent, with the shared key.
func openOwn(peer phonePeer, envelope phoneEnvelope) (phoneBody, error) {
	var body phoneBody
	block, err := aes.NewCipher(peer.key())
	if err != nil {
		return body, err
	}
	gcm, _ := cipher.NewGCM(block)
	nonce, _ := b64.DecodeString(envelope.Nonce)
	box, _ := b64.DecodeString(envelope.Box)
	if len(nonce) != gcm.NonceSize() {
		return body, errors.New("message is malformed")
	}
	plain, err := gcm.Open(nil, nonce, box, envelope.header())
	if err != nil {
		return body, errors.New("message does not open with the shared key")
	}
	return body, json.Unmarshal(plain, &body)
}

func sameBody(a, b phoneBody) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return bytes.Equal(left, right)
}
