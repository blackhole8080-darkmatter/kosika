package main

// Text, numbers, lists and dates, as people use them every day: the
// methods kosika/builtin_methods.py lists after the matrices, word for
// word.

import (
	"fmt"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
)

func capitalized(text string) string {
	runes := []rune(text)
	if len(runes) == 0 {
		return ""
	}
	return strings.ToUpper(string(runes[:1])) + strings.ToLower(string(runes[1:]))
}

var datePattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`)

var weekdayNames = []string{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

// dateOf is a date written YYYY-MM-DD, as `now` gives one.
func dateOf(text, name string) time.Time {
	fail := func() { runFail(fmt.Sprintf("%s needs a date written YYYY-MM-DD, got %s", name, pyRepr(text))) }
	if !datePattern.MatchString(text) {
		fail()
	}
	year, _ := strconv.Atoi(text[:4])
	month, _ := strconv.Atoi(text[5:7])
	day, _ := strconv.Atoi(text[8:])
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if year < 1 || date.Year() != year || int(date.Month()) != month || date.Day() != day {
		fail()
	}
	return date
}

func init() {
	textMethods["capitalize"] = method{nil, func(r Value, a []Value) Value { return capitalized(textOf(r)) }}
	textMethods["title"] = method{nil, func(r Value, a []Value) Value {
		words := strings.Split(textOf(r), " ")
		for i, word := range words {
			words[i] = capitalized(word)
		}
		return strings.Join(words, " ")
	}}
	textMethods["count"] = method{[]string{"part"}, func(r Value, a []Value) Value {
		part := textOf(a[0])
		if part == "" {
			runFail("count needs a part that is not empty")
		}
		return bigInt(strings.Count(textOf(r), part))
	}}
	textMethods["repeat"] = method{[]string{"times"}, func(r Value, a []Value) Value {
		times, ok := asWhole(a[0])
		if !ok || times.Sign() < 0 {
			runFail("repeat needs a whole number that is not negative")
		}
		text := textOf(r)
		if new(big.Int).Mul(times, bigInt(len([]rune(text)))).Cmp(bigInt(maxTextLength)) > 0 {
			runFail(fmt.Sprintf("repeat would exceed %d characters", maxTextLength))
		}
		return strings.Repeat(text, int(times.Int64()))
	}}
	textMethods["reverse"] = method{nil, func(r Value, a []Value) Value {
		runes := []rune(textOf(r))
		for i, j := 0, len(runes)-1; i < j; i, j = i+1, j-1 {
			runes[i], runes[j] = runes[j], runes[i]
		}
		return string(runes)
	}}
	textMethods["add_days"] = method{[]string{"days"}, func(r Value, a []Value) Value {
		days, ok := asWhole(a[0])
		if !ok {
			runFail("add_days needs a whole number of days")
		}
		date := dateOf(textOf(r), "add_days")
		if !days.IsInt64() || days.Int64() > 3_700_000 || days.Int64() < -3_700_000 {
			runFail("add_days goes past the years 1 to 9999")
		}
		moved := date.AddDate(0, 0, int(days.Int64()))
		if moved.Year() < 1 || moved.Year() > 9999 {
			runFail("add_days goes past the years 1 to 9999")
		}
		return moved.Format("2006-01-02")
	}}
	textMethods["days_until"] = method{[]string{"other"}, func(r Value, a []Value) Value {
		from := dateOf(textOf(r), "days_until")
		to := dateOf(textOf(a[0]), "days_until")
		return bigInt(int(math.Round(to.Sub(from).Hours() / 24)))
	}}
	textMethods["weekday"] = method{nil, func(r Value, a []Value) Value {
		date := dateOf(textOf(r), "weekday")
		return weekdayNames[(int(date.Weekday())+6)%7]
	}}
	numberMethods["clamp"] = method{[]string{"low", "high"}, func(r Value, a []Value) Value {
		low, high := a[0], a[1]
		if compareNumbers(low, high) > 0 {
			runFail("clamp needs low to be no more than high")
		}
		if compareNumbers(r, low) < 0 {
			return low
		}
		if compareNumbers(r, high) > 0 {
			return high
		}
		return r
	}}
	numberMethods["log"] = method{nil, func(r Value, a []Value) Value {
		if compareNumbers(r, bigInt(0)) <= 0 {
			runFail("log needs a number above zero")
		}
		return roundTo9(math.Log(toFloat(r))) + 0.0
	}}
	numberMethods["exp"] = method{nil, func(r Value, a []Value) Value {
		result := math.Exp(toFloat(r))
		if math.IsInf(result, 0) {
			runFail("exp gives a number too large")
		}
		return roundTo9(result) + 0.0
	}}
	numberMethods["to_fixed"] = method{[]string{"digits"}, func(r Value, a []Value) Value {
		digits, ok := asWhole(a[0])
		if !ok || digits.Sign() < 0 || digits.Cmp(bigInt(20)) > 0 {
			runFail("to_fixed needs a whole number of digits from 0 to 20")
		}
		return strconv.FormatFloat(toFloat(r), 'f', int(digits.Int64()), 64)
	}}
	listMethods["count"] = method{[]string{"item"}, func(r Value, a []Value) Value {
		count := 0
		for _, candidate := range listOf(r) {
			if valueEqual(candidate, a[0]) {
				count++
			}
		}
		return bigInt(count)
	}}
}
