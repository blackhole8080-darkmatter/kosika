from __future__ import annotations

import unittest
from typing import cast

from kosika.cognitive import (
    MemoryBubble,
    MemoryKind,
    MemoryStatus,
    PermissionScope,
    RetentionPolicy,
)


def memory() -> MemoryBubble:
    return MemoryBubble(
        id="fact",
        content="A checked fact",
        kind=MemoryKind.SEMANTIC,
        source="test",
        confidence=0.8,
        scope=PermissionScope.SESSION,
        retention=RetentionPolicy.SESSION,
    )


class MemoryLifecycleTests(unittest.TestCase):
    def test_promotion_follows_declared_lifecycle_not_alphabetical_values(self) -> None:
        item = memory()
        for expected in (
            MemoryStatus.SUPPORTED,
            MemoryStatus.TRUSTED,
            MemoryStatus.CONTRADICTED,
            MemoryStatus.ARCHIVED,
        ):
            with self.subTest(status=expected):
                item.promote(expected)
                self.assertIs(item.status, expected)

    def test_same_or_earlier_status_does_not_change_version(self) -> None:
        item = memory()
        item.promote(MemoryStatus.UNVERIFIED)
        self.assertIs(item.status, MemoryStatus.UNVERIFIED)
        self.assertEqual(item.version, 1)

        item.promote(MemoryStatus.SUPPORTED)
        item.promote(MemoryStatus.TRUSTED)
        version = item.version
        item.promote(MemoryStatus.SUPPORTED)
        self.assertIs(item.status, MemoryStatus.TRUSTED)
        self.assertEqual(item.version, version)

    def test_promotion_rejects_non_status_values(self) -> None:
        item = memory()
        with self.assertRaisesRegex(TypeError, "MemoryStatus"):
            item.promote(cast(MemoryStatus, "trusted"))
        self.assertIs(item.status, MemoryStatus.UNVERIFIED)
        self.assertEqual(item.version, 1)


if __name__ == "__main__":
    unittest.main()
