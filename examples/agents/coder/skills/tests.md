# How a test file is written
When: python, .py, pytest
Files: test_*.py
Check: contains "def test_"
Check: contains "assert"

This is for Python files only. Ignore it for other languages.

Write tests for pytest. A test is a function whose name starts with test_
and takes no arguments. Use plain assert. Import what you test at the top
of the file, the same way the project's other tests do. Test the normal
case and at least one edge case, such as zero, an empty text or a negative
number. Do not use classes, fixtures or mocks for a small function.
