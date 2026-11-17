"""Kosika networks: cross-device info sharing on top of network.py manifests.
Devices hold a local memory (maths facts) and sync additive facts across a
network. Agent interaction primitives: request, delegate, observe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kosika.network import network_to_dict, new_network_manifest, render_network


def _brain():
    from kosika.brains import build_default_brain
    return build_default_brain()


@dataclass
class Device:
    device_id: str
    kind: str
    brain: Any = field(default_factory=_brain)
    #: vector clock per key for last-write-wins merge
    clock: dict[str, int] = field(default_factory=dict)

    def remember(self, key: str, value: str) -> None:
        self.clock[key] = self.clock.get(key, 0) + 1
        self.brain.memory[key] = {"value": value, "clock": self.clock[key]}

    def recall(self, key: str) -> str | None:
        entry = self.brain.memory.get(key)
        return entry["value"] if entry else None

    def forget(self, key: str) -> None:
        self.brain.memory.pop(key, None)
        self.clock.pop(key, None)

    def keys(self) -> list[str]:
        return list(self.brain.memory.keys())


@dataclass
class Network:
    name: str
    network_id: str
    devices: dict[str, Device] = field(default_factory=dict)
    #: shared broadcast log: list of (device_id, key, value, clock)
    log: list[dict[str, Any]] = field(default_factory=list)

    def add(self, device: Device) -> None:
        self.devices[device.device_id] = device

    def manifest(self) -> dict[str, Any]:
        return network_to_dict(new_network_manifest(self.network_id, self.name))

    def render(self, output_format: str = "text") -> str:
        return render_network(new_network_manifest(self.network_id, self.name), output_format)

    def broadcast(self, from_device: str, key: str, value: str, clock: int) -> None:
        self.log.append({"from": from_device, "key": key, "value": value, "clock": clock})

    def sync(self, a: str, b: str) -> list[str]:
        """Merge facts between two devices (last-write-wins), record changes."""
        da, db = self.devices[a], self.devices[b]
        changed: list[str] = []
        for key in set(da.keys()) | set(db.keys()):
            ea = da.brain.memory.get(key)
            eb = db.brain.memory.get(key)
            ca = da.clock.get(key, 0)
            cb = db.clock.get(key, 0)
            winner = ea if ca > cb else eb
            loser_clock = cb if ca > cb else ca
            winner_clock = ca if ca > cb else cb
            if ca != cb and winner is not None:
                if key not in (db.brain.memory if ca > cb else da.brain.memory) or loser_clock < winner_clock:
                    target = db.brain.memory if ca > cb else da.brain.memory
                    target[key] = {"value": winner["value"], "clock": winner_clock}
                    (db if ca > cb else da).clock[key] = winner_clock
                    changed.append(key)
                    self.broadcast(a if ca > cb else b, key, winner["value"], winner_clock)
        return changed

    def interact(self, actor: str, action: str, key: str, value: str = "") -> dict[str, Any]:
        """Agent interaction primitive: request/delegate/observe a fact."""
        dev = self.devices[actor]
        if action == "request":
            return {"actor": actor, "action": "request", "key": key, "value": dev.recall(key)}
        if action == "delegate":
            dev.remember(key, value)
            return {"actor": actor, "action": "delegate", "key": key, "value": value, "stored": True}
        if action == "observe":
            return {"actor": actor, "action": "observe", "key": key, "value": dev.recall(key), "keys": dev.keys()}
        return {"actor": actor, "action": action, "error": "unknown action"}


def demo_network() -> Network:
    net = Network("kosika-demo-net", "kosika-demo-net")
    net.add(Device("device-a", "kosika"))
    net.add(Device("device-b", "kosika"))
    # Device A learns a maths fact through the brains maths skill
    net.devices["device-a"].remember("deriv.x2sinx", "2x sin(x) + x^2 cos(x)")
    net.devices["device-a"].remember("deriv.x3", "3x^2")
    # Device B observes A's knowledge
    print(net.interact("device-b", "observe", "deriv.x2sinx"))
    # Sync A -> B
    changed = net.sync("device-a", "device-b")
    print("synced keys:", changed)
    print("B recalls deriv.x2sinx:", net.devices["device-b"].recall("deriv.x2sinx"))
    print("B recalls deriv.x3:", net.devices["device-b"].recall("deriv.x3"))
    # B delegates a new fact back
    print(net.interact("device-b", "delegate", "deriv.sin", "cos(x)"))
    net.sync("device-b", "device-a")
    print("A recalls deriv.sin:", net.devices["device-a"].recall("deriv.sin"))
    return net
