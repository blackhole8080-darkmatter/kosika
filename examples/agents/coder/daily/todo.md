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
