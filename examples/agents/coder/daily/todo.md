# Kosika's to-do, for the coder

One request a line. `python daily.py` takes the first open one (`- [ ]`),
works on a copy of Kosika, and marks it `[x]` (its checks passed) or `[!]`
(they did not), with the folder that holds its change and report. Nothing
here reaches Kosika until its owner applies the change.

- [x] add a Text method is_digits() that says whether a text is not empty and every character in it is a digit 0 to 9, giving a Bool (2026-11-06, 2026-11-06-4)
- [x] add a function word_count(text: Text) -> Number to kosika/std/text.kos that gives how many words a text has, as words_of counts them (2026-11-06, 2026-11-06-5)
- [x] add a Number method sign() that gives -1 for a negative number, 0 for zero and 1 for a positive one (2026-11-10, 2026-11-10-2)
- [x] add a Text method last_index_of(part: Text) that gives a Number?: where part last begins in the text, counted from 0, or null when it is not in it; part must not be empty, as for index_of (2026-11-10, 2026-11-10-7)
- [x] add a Text method count_lines() that gives how many lines a text has, 0 for an empty text, and does not count a line break at the very end as a new line (2026-11-11, 2026-11-11-1)
- [x] add a Text method starts_with_any(prefixes: Text[]) that says whether the text begins with any of the prefixes, giving a Bool; an empty list gives false (2026-11-14, 2026-11-14-1)
- [!] add a Number method is_even() that says whether a whole number is even, giving a Bool; a number that is not whole gives false (2026-11-14, 2026-11-14-2)
- [x] add a function initials(text: Text) -> Text to kosika/std/text.kos that gives the first letter of each word, in upper case, as words_of finds the words (2026-11-11, 2026-11-11-4)
- [!] add a Text method surround(left: Text, right: Text) that gives the text with left before it and right after it (2026-11-14, 2026-11-14-3)
- [x] add a Text method is_hex_colour() that says whether the text is a colour written as # followed by exactly 3 or 6 hexadecimal digits (0 to 9, a to f, A to F), giving a Bool (2026-11-14, 2026-11-14-4)
- [!] add a Text method hex_colours() that gives, in order, every colour in the text written as # followed by exactly 6 hexadecimal digits and not followed by another hexadecimal digit, as a Text[] (2026-11-14, 2026-11-14-5)
- [!] add a Number method round_to(places: Number) that gives the number rounded to that many decimal places, halves away from zero; places must be a whole number from 0 to 10 (2026-11-14, 2026-11-14-6)
- [ ] add a Text method pad_start(width: Number, fill: Text) that gives the text with fill put before it until it is width characters long; fill must be exactly one character, and a text already that long is given back unchanged
- [ ] add a Text method pad_end(width: Number, fill: Text) that gives the text with fill put after it until it is width characters long; fill must be exactly one character, and a text already that long is given back unchanged
- [ ] add a function title_case(text: Text) -> Text to kosika/std/text.kos that gives each word with its first letter in upper case and the rest unchanged, as words_of finds the words, joined by single spaces
- [ ] add a Text method truncate(most: Number) that gives the text unchanged when it has at most `most` characters, else its first most - 1 characters followed by "…"; most must be a whole number of at least 1
- [ ] add an exported function hex_of(red: Number, green: Number, blue: Number) -> Text to kosika/std/designs_palette.kos that gives the colour as # and 6 lower-case hexadecimal digits; each channel is rounded and kept within 0 to 255
- [ ] add an exported function mix(a: Text, b: Text, part: Number) -> Text to kosika/std/designs_palette.kos that mixes two #rrggbb colours channel by channel, part 0 giving a and part 1 giving b, part kept within 0 to 1, the result written as hex_of writes it
- [ ] add exported functions lighter(colour: Text, part: Number) -> Text and darker(colour: Text, part: Number) -> Text to kosika/std/designs_palette.kos that mix a #rrggbb colour with #ffffff or #000000 by part, as mix does
- [ ] add an exported function readable_on(text_colour: Text, background: Text) -> Bool to kosika/std/designs_palette.kos that says whether the two #rrggbb colours have a contrast of at least 4.5, as contrast measures it
- [ ] add a function slug(text: Text) -> Text to kosika/std/text.kos that gives the words of the text, as words_of finds them, in lower case, with every character that is not a to z or 0 to 9 left out, joined by "-"
- [ ] add a Text method is_hex_colour() that says whether the text is a colour written as # and then 3 or 6 of the digits 0 to 9 and letters a to f, either case, giving a Bool
- [ ] add a function palette_has(name: Text) -> Bool to kosika/std/designs_palette.kos that says whether a palette of that name exists, matching names as palette_named does
- [ ] add a function hues_of(name: Text) -> Text[] to kosika/std/designs_palette.kos that gives the hues of the palette of that name, or the first palette's hues when there is none, as palette_named chooses
- [ ] add a Text method is_upper_letter() that says whether the text is exactly one letter from A to Z, giving a Bool
