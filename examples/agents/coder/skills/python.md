# How a function is added to a Python file
When: function, add a, python, .py, method
Files: *.py
Check: contains "def "

Keep every function the file already has, unchanged, and add the new one
after them. Give the new function a one-line docstring. Use type hints if
the file's other functions use them, and not if they do not. Do not print
from a function that is meant to return a value. Do not add a main block
unless the file already has one.
