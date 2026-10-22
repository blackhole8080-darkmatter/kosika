package main

// Reading web pages, from sites the person running the program chose, as
// kosika/web.py reads them: one GET of an https address on an allowed site,
// redirects only to other allowed addresses, at most 2 MiB of text/html,
// text/plain or a feed, reduced to its readable text. A page is a
// stranger's text and stays data: a Text value like any other.

import (
	"bytes"
	"compress/gzip"
	"crypto/tls"
	"fmt"
	"html"
	"io"
	"math"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	webMaxBytes      = 2 * 1024 * 1024
	webMaxCharacters = 20_000
	webMaxRedirects  = 5
	webTimeout       = 20 * time.Second
	webUserAgent     = "Kosika/0.1 (read-only page fetch for a local assistant)"
)

var feedTypes = []string{"application/atom+xml", "application/rss+xml", "application/xml", "text/xml"}

// webFail ends the step, as a WebError ends it in the reference.
func webFail(format string, args ...any) {
	runFail(fmt.Sprintf(format, args...))
}

var siteName = regexp.MustCompile(`^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$`)

// cleanSite is a site as the runner may write it, reduced to a bare host
// name, or "" with the reason it is not one.
func cleanSite(site string) (string, string) {
	host := strings.ToLower(strings.TrimSpace(site))
	host = regexp.MustCompile(`^https?://`).ReplaceAllString(host, "")
	host = strings.TrimRight(strings.SplitN(host, "/", 2)[0], ".")
	if host == "" || strings.Contains(host, ":") || strings.Contains(host, "@") || !siteName.MatchString(host) || !strings.Contains(host, ".") {
		return "", fmt.Sprintf("%s is not a site name such as en.wikipedia.org", pyRepr(site))
	}
	if net.ParseIP(host) != nil {
		return "", fmt.Sprintf("%s is an address, not a site name", pyRepr(site))
	}
	return host, ""
}

// allowedAddress is url when it may be fetched, or "" with why not.
func allowedAddress(address string, sites []string) (string, string) {
	trimmed := strings.TrimSpace(address)
	parts, err := url.Parse(trimmed)
	if err != nil {
		return "", fmt.Sprintf("%s is not a web address", pyRepr(address))
	}
	port := parts.Port()
	if port != "" {
		if _, err := strconv.Atoi(port); err != nil {
			return "", fmt.Sprintf("%s is not a web address", pyRepr(address))
		}
	}
	host := strings.TrimRight(strings.ToLower(parts.Hostname()), ".")
	if parts.Scheme != "https" {
		return "", fmt.Sprintf("%s must start with https://", pyRepr(address))
	}
	if parts.User != nil || (port != "" && port != "443") || host == "" {
		return "", fmt.Sprintf("%s is not a plain https address", pyRepr(address))
	}
	if net.ParseIP(host) != nil {
		return "", fmt.Sprintf("%s names an address, not a site", pyRepr(address))
	}
	for _, site := range sites {
		if host == site || strings.HasSuffix(host, "."+site) {
			return trimmed, ""
		}
	}
	allowed := strings.Join(sites, ", ")
	if allowed == "" {
		allowed = "none"
	}
	return "", fmt.Sprintf("%s is not one of the allowed sites (%s)", host, allowed)
}

// An htmlEvent is one thing a page says: a start tag with its attributes,
// an end tag, or text, as Python's html.parser reports them.
type htmlEvent struct {
	kind  string // "start", "end" or "data"
	tag   string
	attrs map[string]string
	data  string
}

var htmlAttribute = regexp.MustCompile(`([^\s/>"'=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]*))?`)

// htmlEvents reads a page as Python's html.parser reads it, for what the
// readers below need: tags in lower case, character references made
// characters, comments, declarations and CDATA sections dropped, script
// and style content kept whole, and `<x/>` as a start and an end.
func htmlEvents(body string) []htmlEvent {
	var events []htmlEvent
	text := func(data string) {
		if data != "" {
			events = append(events, htmlEvent{kind: "data", data: html.UnescapeString(data)})
		}
	}
	i := 0
	start := 0
	for i < len(body) {
		if body[i] != '<' {
			i++
			continue
		}
		rest := body[i:]
		switch {
		case strings.HasPrefix(rest, "<!--"):
			text(body[start:i])
			end := strings.Index(rest[4:], "-->")
			if end < 0 {
				return events
			}
			i += 4 + end + 3
			start = i
		case strings.HasPrefix(rest, "<![CDATA["):
			text(body[start:i])
			end := strings.Index(rest, "]]>")
			if end < 0 {
				return events
			}
			i += end + 3
			start = i
		case strings.HasPrefix(rest, "<!") || strings.HasPrefix(rest, "<?"):
			text(body[start:i])
			end := strings.IndexByte(rest, '>')
			if end < 0 {
				return events
			}
			i += end + 1
			start = i
		case strings.HasPrefix(rest, "</") && len(rest) > 2 && isTagStart(rest[2]):
			text(body[start:i])
			end := strings.IndexByte(rest, '>')
			if end < 0 {
				return events
			}
			name := strings.ToLower(strings.TrimSpace(strings.Fields(rest[2:end] + " ")[0]))
			events = append(events, htmlEvent{kind: "end", tag: name})
			i += end + 1
			start = i
		case len(rest) > 1 && isTagStart(rest[1]):
			text(body[start:i])
			end := tagEnd(rest)
			if end < 0 {
				return events
			}
			inside := rest[1:end]
			closing := strings.HasSuffix(inside, "/")
			inside = strings.TrimSuffix(inside, "/")
			nameEnd := strings.IndexAny(inside, " \t\n\r\f/")
			if nameEnd < 0 {
				nameEnd = len(inside)
			}
			name := strings.ToLower(inside[:nameEnd])
			attrs := map[string]string{}
			for _, found := range htmlAttribute.FindAllStringSubmatch(inside[nameEnd:], -1) {
				value := found[2]
				if len(value) >= 2 && (value[0] == '"' || value[0] == '\'') {
					value = value[1 : len(value)-1]
				}
				key := strings.ToLower(found[1])
				if _, seen := attrs[key]; !seen {
					attrs[key] = html.UnescapeString(value)
				}
			}
			events = append(events, htmlEvent{kind: "start", tag: name, attrs: attrs})
			i += end + 1
			start = i
			if closing {
				events = append(events, htmlEvent{kind: "end", tag: name})
			} else if name == "script" || name == "style" {
				// Their content is text up to their own end tag.
				lower := strings.ToLower(body[i:])
				close := strings.Index(lower, "</"+name)
				if close < 0 {
					text(body[i:])
					return events
				}
				text(body[i : i+close])
				i += close
				start = i
			}
		default:
			i++
		}
	}
	text(body[start:])
	return events
}

func isTagStart(b byte) bool {
	return b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z'
}

// tagEnd is where a start tag's `>` is, past any quoted attribute value.
func tagEnd(tag string) int {
	var quote byte
	for i := 1; i < len(tag); i++ {
		switch {
		case quote != 0:
			if tag[i] == quote {
				quote = 0
			}
		case tag[i] == '"' || tag[i] == '\'':
			quote = tag[i]
		case tag[i] == '>':
			return i
		}
	}
	return -1
}

var (
	textSkip  = setOf("script style noscript template svg head nav header footer aside form button select")
	textMain  = setOf("main article")
	textBreak = setOf("p br div li tr h1 h2 h3 h4 h5 h6 section blockquote pre table ul ol")
)

func setOf(words string) map[string]bool {
	out := map[string]bool{}
	for _, word := range strings.Fields(words) {
		out[word] = true
	}
	return out
}

func tidyText(parts []string) string {
	var lines []string
	for _, line := range strings.Split(pyText([]byte(strings.Join(parts, ""))), "\n") {
		if joined := strings.Join(strings.Fields(line), " "); joined != "" {
			lines = append(lines, joined)
		}
	}
	return strings.Join(lines, "\n")
}

// readablePage is a page body's title and whole readable text: no scripts,
// styles, menus or markup; just what a page marks as its content when it
// marks enough.
func readablePage(body string, isHTML bool) (string, string) {
	if !isHTML {
		return "", pyStrip(body)
	}
	var parts, main, title []string
	skipping, inMain, inTitle := 0, 0, false
	add := func(text string) {
		parts = append(parts, text)
		if inMain > 0 {
			main = append(main, text)
		}
	}
	for _, e := range htmlEvents(body) {
		switch e.kind {
		case "start":
			switch {
			case e.tag == "title":
				inTitle = true
			case textSkip[e.tag]:
				skipping++
			case textMain[e.tag]:
				inMain++
				add("\n")
			case textBreak[e.tag]:
				add("\n")
			}
		case "end":
			switch {
			case e.tag == "title":
				inTitle = false
			case textSkip[e.tag] && skipping > 0:
				skipping--
			case textMain[e.tag] && inMain > 0:
				inMain--
			case textBreak[e.tag]:
				add("\n")
			}
		case "data":
			if inTitle {
				title = append(title, e.data)
			} else if skipping == 0 {
				add(e.data)
			}
		}
	}
	text := tidyText(main)
	if utf8.RuneCountInString(text) < 200 {
		text = tidyText(parts)
	}
	return strings.Join(strings.Fields(strings.Join(title, "")), " "), text
}

var feedLabels = map[string]string{"title": "Title", "published": "Published", "pubdate": "Published", "updated": "Updated", "name": "Author", "summary": "Summary", "description": "Summary"}

// readableFeed is a feed as plain lines: each entry begins with `---`, then
// a line for each of its title, date, authors, link and summary.
func readableFeed(body string) string {
	var lines []string
	entry := 0
	label := ""
	var said []string
	for _, e := range htmlEvents(body) {
		switch e.kind {
		case "start":
			switch {
			case e.tag == "entry" || e.tag == "item":
				entry++
				lines = append(lines, "---")
			case entry > 0 && feedLabels[e.tag] != "":
				label, said = feedLabels[e.tag], nil
			case entry > 0 && e.tag == "link":
				rel, has := e.attrs["rel"]
				if !has {
					rel = "alternate"
				}
				if e.attrs["href"] != "" && rel == "alternate" {
					lines = append(lines, "Link: "+e.attrs["href"])
				}
			}
		case "end":
			switch {
			case (e.tag == "entry" || e.tag == "item") && entry > 0:
				entry--
			case label != "" && feedLabels[e.tag] == label:
				if text := strings.Join(strings.Fields(strings.Join(said, "")), " "); text != "" {
					lines = append(lines, label+": "+text)
				}
				label = ""
			}
		case "data":
			if label != "" {
				said = append(said, e.data)
			}
		}
	}
	return strings.Join(lines, "\n")
}

// decodeBody is a page's bytes as text, in the charset its type names:
// UTF-8 unless it names Latin-1 or ASCII, a bad byte made U+FFFD.
func decodeBody(body []byte, contentType string) string {
	charset := ""
	if found := regexp.MustCompile(`charset=([\w-]+)`).FindStringSubmatch(contentType); found != nil {
		charset = strings.ToLower(found[1])
	}
	switch charset {
	case "iso-8859-1", "latin-1", "latin1", "l1":
		runes := make([]rune, len(body))
		for i, b := range body {
			runes[i] = rune(b)
		}
		return string(runes)
	}
	return strings.ToValidUTF8(string(body), "�")
}

// webPage is the whole readable text of one page from an allowed site:
// _whole in kosika/web.py.
func (r *run) webPage(address string) (string, string, string) {
	current, problem := allowedAddress(address, r.sites)
	if problem != "" {
		webFail("%s", problem)
	}
	client := &http.Client{
		Timeout:       webTimeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		// Nothing is asked to come packed, as the reference asks nothing.
		Transport: &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}, Proxy: nil, DisableCompression: true},
	}
	for range webMaxRedirects + 1 {
		request, err := http.NewRequest("GET", current, nil)
		if err != nil {
			webFail("%s is not a web address", pyRepr(current))
		}
		request.Header.Set("User-Agent", webUserAgent)
		request.Header.Set("Accept", "text/html, text/plain, application/atom+xml, application/rss+xml")
		response, err := client.Do(request)
		if err != nil {
			webFail("Could not reach %s: %v", current, err)
		}
		status := response.StatusCode
		if status == 301 || status == 302 || status == 303 || status == 307 || status == 308 {
			response.Body.Close()
			target := response.Header.Get("Location")
			if target == "" {
				webFail("%s redirected to nowhere", current)
			}
			base, _ := url.Parse(current)
			next, err := base.Parse(target)
			if err != nil {
				webFail("%s is not a web address", pyRepr(target))
			}
			if current, problem = allowedAddress(next.String(), r.sites); problem != "" {
				webFail("%s", problem)
			}
			continue
		}
		body, _ := io.ReadAll(io.LimitReader(response.Body, webMaxBytes+1))
		response.Body.Close()
		if status != 200 {
			webFail("%s answered with status %d", current, status)
		}
		contentType := response.Header.Get("Content-Type")
		kind := strings.ToLower(strings.TrimSpace(strings.SplitN(contentType, ";", 2)[0]))
		if kind != "text/html" && kind != "text/plain" && kind != "application/xhtml+xml" && !hasName(feedTypes, kind) {
			shown := kind
			if shown == "" {
				shown = "of unknown type"
			}
			webFail("%s is %s, not a text page", current, shown)
		}
		if encoding := strings.ToLower(strings.TrimSpace(response.Header.Get("Content-Encoding"))); encoding == "gzip" || encoding == "x-gzip" {
			// Sent packed although nothing asked for it, as some sites do.
			unpacker, err := gzip.NewReader(bytes.NewReader(body))
			if err != nil {
				webFail("%s sent a packed page that cannot be read", current)
			}
			body, err = io.ReadAll(io.LimitReader(unpacker, webMaxBytes+1))
			if err != nil && err != io.ErrUnexpectedEOF {
				webFail("%s sent a packed page that cannot be read", current)
			}
		}
		if len(body) > webMaxBytes {
			webFail("%s is larger than 2 MiB", current)
		}
		decoded := decodeBody(body, contentType)
		if hasName(feedTypes, kind) {
			return current, "", readableFeed(decoded)
		}
		title, text := readablePage(decoded, kind != "text/plain")
		return current, title, text
	}
	webFail("%s redirected more than %d times", address, webMaxRedirects)
	return "", "", ""
}

func firstCharacters(text string, start, most int) string {
	runes := []rune(text)
	if start > len(runes) {
		start = len(runes)
	}
	end := start + most
	if end > len(runes) {
		end = len(runes)
	}
	return string(runes[start:end])
}

// webTool reads a page for a `system "web"` tool: `fetch` gives its first
// 20,000 characters, `fetch_from` those from `start`, with where the next
// part begins, or -1.
func (r *run) webTool(tool *ToolDecl, arguments *Record) Value {
	address, _ := arguments.Fields["url"].(string)
	result := newRecord()
	if tool.Operation == "fetch_from" {
		start := arguments.Fields["start"]
		begin, fine := 0, false
		switch n := start.(type) {
		case *big.Int:
			if n.Sign() >= 0 && n.IsInt64() {
				begin, fine = int(n.Int64()), true
			}
		case float64:
			if n >= 0 && n == math.Trunc(n) && n < 1e15 {
				begin, fine = int(n), true
			}
		}
		if !fine {
			webFail("start must be a whole number from 0, got %s", pyNumber(start))
		}
		current, title, text := r.webPage(address)
		length := utf8.RuneCountInString(text)
		result.set("url", current)
		result.set("title", title)
		result.set("text", firstCharacters(text, begin, webMaxCharacters))
		next := begin + webMaxCharacters
		if next >= length {
			next = -1
		}
		result.set("next", bigInt(next))
		return result
	}
	current, title, text := r.webPage(address)
	result.set("url", current)
	result.set("title", title)
	result.set("text", firstCharacters(text, 0, webMaxCharacters))
	return result
}
