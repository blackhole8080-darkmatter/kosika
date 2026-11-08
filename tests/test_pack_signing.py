"""Signing a pack: it proves who made it, and is refused when it does not fit."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.pack import (
    PackError,
    check_pack,
    read_key,
    sign_pack,
    to_text,
    write_pack,
)
from kosika.signature import public_key, sign, verify

#: RFC 8032 section 7.1: seed, public key, message, signature.
VECTORS = (
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
)

PROGRAM = """
record Ticket { text: Text }
record Out { text: Text }

fn make(ticket: Ticket) -> Out {
  return {text: ticket.text}
}

workflow main(ticket: Ticket) -> Out {
  out = make(ticket)
}
"""


class SignatureTests(unittest.TestCase):
    def test_the_rfc_vectors_sign_and_verify(self) -> None:
        for seed_text, public_text, message_hex, signature_text in VECTORS:
            with self.subTest(seed=seed_text[:8]):
                seed = bytes.fromhex(seed_text)
                public = bytes.fromhex(public_text)
                message = bytes.fromhex(message_hex)
                made = bytes.fromhex(signature_text)
                self.assertEqual(public_key(seed), public)
                self.assertEqual(sign(seed, message), made)
                self.assertTrue(verify(public, message, made))

    def test_a_changed_message_does_not_verify(self) -> None:
        seed, public_text, message_hex, signature_text = VECTORS[1]
        public = bytes.fromhex(public_text)
        made = bytes.fromhex(signature_text)
        self.assertFalse(verify(public, bytes.fromhex(message_hex) + b"x", made))

    def test_another_key_does_not_verify(self) -> None:
        first = bytes.fromhex(VECTORS[0][0])
        second = bytes.fromhex(VECTORS[1][0])
        made = sign(first, b"a thing said")
        self.assertTrue(verify(public_key(first), b"a thing said", made))
        self.assertFalse(verify(public_key(second), b"a thing said", made))

    def test_a_malformed_key_or_signature_is_false_not_an_error(self) -> None:
        public = bytes.fromhex(VECTORS[0][1])
        made = bytes.fromhex(VECTORS[0][3])
        for bad_key in (b"", b"\x00" * 31, b"\xff" * 32):
            with self.subTest(key=len(bad_key)):
                self.assertFalse(verify(bad_key, b"", made))
        for bad_made in (b"", made[:-1], b"\x00" * 64, made[:32] + b"\xff" * 32):
            with self.subTest(signature=len(bad_made)):
                self.assertFalse(verify(public, b"", bad_made))

    def test_a_seed_of_the_wrong_size_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            public_key(b"short")
        with self.assertRaises(ValueError):
            sign(b"short", b"a thing said")


class PackSignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        source = self.root / "main.kos"
        source.write_text(PROGRAM, encoding="utf-8")
        self.pack = self.root / "helper.kpack"
        write_pack(source, self.pack)
        self.key = self.root / ".kosika" / "pack-key.json"

    def changed(self, change: Any) -> Path:
        pack = json.loads(self.pack.read_text("utf-8"))
        change(pack)
        path = self.root / "changed.kpack"
        path.write_text(json.dumps(pack), encoding="utf-8")
        return path

    def test_signing_records_the_signer_and_the_key_is_kept(self) -> None:
        signed = sign_pack(self.pack, self.key)
        public_text = signed["signature"]["public_key"]
        self.assertEqual(
            sorted(signed["signature"]), ["alg", "public_key", "signature"]
        )
        self.assertEqual(read_key(self.key), read_key(self.key))
        report = check_pack(self.pack, require_signature=True)
        self.assertEqual(report.signed_by, public_text)
        self.assertIn("Signer   ed25519 ", to_text(report))
        self.assertEqual(report.to_dict()["signed_by"], public_text)

    def test_a_pack_never_carries_the_seed(self) -> None:
        sign_pack(self.pack, self.key)
        seed = read_key(self.key)[0]
        self.assertNotIn(seed.hex(), self.pack.read_text("utf-8"))

    def test_a_changed_program_hash_is_refused(self) -> None:
        sign_pack(self.pack, self.key)

        def change(pack: dict[str, Any]) -> None:
            pack["program_hash"] = "0" * 64

        with self.assertRaisesRegex(PackError, "signature does not match"):
            check_pack(self.changed(change))

    def test_a_changed_module_with_its_hash_made_to_match_is_refused(self) -> None:
        sign_pack(self.pack, self.key)

        def change(pack: dict[str, Any]) -> None:
            text = pack["modules"]["main.kos"].replace(
                "return {text: ticket.text}", 'return {text: "changed"}'
            )
            pack["modules"]["main.kos"] = text
            pack["module_hashes"]["main.kos"] = hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest()

        with self.assertRaisesRegex(PackError, "signature does not match"):
            check_pack(self.changed(change))

    def test_an_unsigned_pack_is_refused_only_when_a_signature_is_required(
        self,
    ) -> None:
        self.assertIsNone(check_pack(self.pack).signed_by)
        with self.assertRaisesRegex(PackError, "not signed"):
            check_pack(self.pack, require_signature=True)

    def test_a_signature_naming_an_unknown_algorithm_is_refused(self) -> None:
        sign_pack(self.pack, self.key)

        def change(pack: dict[str, Any]) -> None:
            pack["signature"]["alg"] = "rsa"

        with self.assertRaisesRegex(PackError, "unknown algorithm"):
            check_pack(self.changed(change))

    def test_a_key_file_that_is_not_a_key_is_refused_and_left_alone(self) -> None:
        self.key.parent.mkdir(parents=True)
        self.key.write_text("{}", encoding="utf-8")
        before = self.key.read_text("utf-8")
        with self.assertRaisesRegex(PackError, "not a kosika.pack-key/v1 key"):
            sign_pack(self.pack, self.key)
        self.assertEqual(self.key.read_text("utf-8"), before)

    def test_a_key_that_does_not_match_its_public_key_is_refused(self) -> None:
        sign_pack(self.pack, self.key)
        data = json.loads(self.key.read_text("utf-8"))
        data["public_key"] = VECTORS[0][1]
        self.key.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(PackError, "does not match its public key"):
            sign_pack(self.pack, self.key)


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        source = self.root / "main.kos"
        source.write_text(PROGRAM, encoding="utf-8")
        self.pack = self.root / "a.kpack"
        write_pack(source, self.pack)
        self.key = self.root / "pack-key.json"

    def invoke(self, *arguments: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["kosika", *arguments]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main()
        return code, out.getvalue() + err.getvalue()

    def test_sign_then_check_with_a_required_signature(self) -> None:
        code, output = self.invoke(
            "pack", "sign", str(self.pack), "--key", str(self.key)
        )
        self.assertEqual(code, 0, output)
        self.assertIn("Signed ", output)
        code, output = self.invoke(
            "pack", "check", str(self.pack), "--require-signature", "--json"
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(len(json.loads(output)["signed_by"]), 64)

    def test_requiring_a_signature_refuses_an_unsigned_pack(self) -> None:
        code, output = self.invoke(
            "pack", "check", str(self.pack), "--require-signature"
        )
        self.assertEqual(code, 2)
        self.assertIn("not signed", output)


if __name__ == "__main__":
    unittest.main()
