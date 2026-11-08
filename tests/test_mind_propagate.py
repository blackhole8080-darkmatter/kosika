# tests/test_mind_propagate.py
import json

from kosika.mind import propagate


def test_propagate_writes_one_delta_per_peer_inbox(tmp_path):
    a = tmp_path / "node-a" / "inbox"
    b = tmp_path / "node-b" / "inbox"
    written = propagate({"rule": "photosynthesis needs light"}, [a, b])
    assert written == (a.parent.name, b.parent.name)
    for inbox in (a, b):
        files = list(inbox.glob("mind-delta-*.json"))
        assert len(files) == 1
        assert (
            json.loads(files[0].read_text(encoding="utf-8"))["rule"]
            == "photosynthesis needs light"
        )


def test_propagate_to_no_peers_writes_nothing(tmp_path):
    assert propagate({"rule": "x"}, []) == ()
