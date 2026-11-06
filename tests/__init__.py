"""Kosika's tests.

Some modules import helpers from a sibling (`from tests.test_tool_loop
import SCOUT`), which needs this directory to be a package rather than a bare
folder of scripts; without it pytest cannot collect those modules at all.
"""
