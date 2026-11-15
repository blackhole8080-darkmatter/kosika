# How a function is added to a Python file
When: python, .py
Files: *.py
Check: contains "def "

This is for Python files only. Ignore it for other languages.

Keep every function the file already has, unchanged, and add the new one
after them. Give the new function a one-line docstring. Use type hints if
the file's other functions use them, and not if they do not. Do not print
from a function that is meant to return a value. Do not add a main block
unless the file already has one.
