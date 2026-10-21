"""`system "linux"` and `system "computer"`: tools that only look at how a
Linux the person named, and this computer, are doing."""

from __future__ import annotations

import sys
import types
import unittest

from kosika.checker import CheckError, check
from kosika.parser import parse
from kosika.runtime import run
from kosika.system_tools import SystemToolError, bind_system_tools

SOURCE = """
record Linux { distro: Text kernel: Text uptime: Text load: Text memory: Text disk: Text upgrades: Text top: Text[] }
record Computer { name: Text system: Text cores: Number memory: Text disk: Text uptime: Text }
record Ask { text: Text }

tool linux_status {
  source system "linux"
  operation: "status"
  access: observe
  output: Linux
}

tool computer_status {
  source system "computer"
  operation: "status"
  access: observe
  output: Computer
}

agent eyes {
  can: [linux_status, computer_status]
  max_steps: 1
}

workflow linux(a: Ask) -> Linux {
  now = execute eyes.linux_status()
}

workflow computer(a: Ask) -> Computer {
  now = execute eyes.computer_status()
}
"""


class StandIns:
    """The readers MayaOS keeps (kosika.wsl, kosika.mayaos), stood in for."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.saved = {name: sys.modules.get(name) for name in ("kosika.wsl", "kosika.mayaos")}
        wsl = types.ModuleType("kosika.wsl")

        class WslError(RuntimeError):
            pass

        def status(distro: str) -> dict:
            self.asked.append(distro)
            if distro == "Gone":
                raise WslError("Linux (Gone) did not answer: no such distribution")
            return {"distro": distro, "kernel": "Linux 6.1", "uptime": "up 2 hours", "load": "0.1 0.1 0.1",
                    "memory": "600 of 7500 MB used", "disk": "4G of 1T used (1%) on /", "upgrades": "3",
                    "top": ["python3 4.0%", "sshd 0.1%"]}

        wsl.WslError, wsl.status = WslError, status
        mayaos = types.ModuleType("kosika.mayaos")
        mayaos.windows = lambda: {"name": "HOME-PC", "system": "Windows 11", "version": "10.0", "cores": 8,
                                  "memory": "8000 of 16000 MB used", "disk": "300 of 500 GB used on C:",
                                  "uptime": "2 d 3 h 4 min"}
        sys.modules["kosika.wsl"], sys.modules["kosika.mayaos"] = wsl, mayaos

    def restore(self) -> None:
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class ReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.program = parse(SOURCE)
        check(self.program)
        self.readers = StandIns()
        self.addCleanup(self.readers.restore)

    def ask(self, workflow: str, **grants: object) -> dict:
        tools = bind_system_tools(self.program, folder=None, **grants)  # type: ignore[arg-type]
        return run(self.program, workflow, None, inputs={"a": {"text": "how"}}, tool_bindings=tools)

    def test_the_named_linux_is_read_and_nothing_else(self) -> None:
        found = self.ask("linux", linux="Ubuntu")
        self.assertEqual(found["distro"], "Ubuntu")
        self.assertEqual(found["top"], ["python3 4.0%", "sshd 0.1%"])
        self.assertEqual(self.readers.asked, ["Ubuntu"])

    def test_this_computer_is_read_without_the_fields_it_does_not_declare(self) -> None:
        found = self.ask("computer", computer=True)
        self.assertEqual(found, {"name": "HOME-PC", "system": "Windows 11", "cores": 8,
                                 "memory": "8000 of 16000 MB used", "disk": "300 of 500 GB used on C:",
                                 "uptime": "2 d 3 h 4 min"})

    def test_without_a_grant_they_reach_nothing(self) -> None:
        tools = bind_system_tools(self.program, folder=None)
        self.assertNotIn("linux_status", tools)
        self.assertNotIn("computer_status", tools)

    def test_a_linux_that_does_not_answer_fails_the_step(self) -> None:
        with self.assertRaisesRegex(Exception, "did not answer"):
            self.ask("linux", linux="Gone")

    def test_only_a_plain_distribution_name_is_granted(self) -> None:
        with self.assertRaisesRegex(SystemToolError, "is not a plain name"):
            bind_system_tools(self.program, folder=None, linux="Ubuntu; rm -rf /")

    def test_their_declarations_are_held_to_their_one_operation_and_shape(self) -> None:
        for change, said in (
            ('operation: "status"\n  access: observe\n  output: Linux', "unknown linux operation"),
            ("output: Linux", "must have exactly the fields {distro: Text"),
            ("output: Computer", "must have exactly the fields {name: Text, system: Text, cores: Number"),
        ):
            bad = SOURCE.replace(change, change.replace('"status"', '"reboot"').replace("Linux", "Ask").replace("Computer", "Ask"), 1)
            with self.subTest(said=said), self.assertRaisesRegex(CheckError, said):
                check(parse(bad))


if __name__ == "__main__":
    unittest.main()
