#!/usr/bin/env python3
"""The index is the one list a hiding registry cannot edit, so its appender
must not be the thing that corrupts it.

Each test here is a way the publisher could have been quietly wrong:

  * ordering layer ids as STRINGS, which puts 2026.09.12 before 2026.09.2 and
    was wrong by eight layers when a real realm was measured that way;
  * bumping the document counter on a re-dispatch, which makes the next
    legitimate publish look stale to a consumer that refuses an older index;
  * rewriting an entry when a layer id turns up with different bytes, which
    hides exactly what varve's immutability rule exists to catch.

Run: python3 -m unittest discover -s tools -p 'test_*.py'
"""
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "append_index", pathlib.Path(__file__).parent / "append-index.py"
)
append_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(append_index)

ISSUED = "2026-10-01T00:00:00Z"


def doc(line="2026.09", counter=1, layers=()):
    return {"line": line, "counter": counter, "issued-at": ISSUED,
            "layers": [dict(l) for l in layers]}


def entry(layer, digest="sha256:" + "a" * 64, channel="rolling", counter=1):
    return {"layer": layer, "digest": digest, "channel": channel, "counter": counter}


class Ordering(unittest.TestCase):
    def test_layers_are_ordered_numerically_not_lexicographically(self):
        """2026.09.2 is OLDER than 2026.09.12, and a string sort disagrees."""
        d = doc(layers=[entry("2026.09.12", "sha256:" + "1" * 64)])
        d, changed = append_index.append(
            d, "2026.09.2", "sha256:" + "2" * 64, "rolling", 2, ISSUED
        )
        self.assertTrue(changed)
        self.assertEqual(
            [l["layer"] for l in d["layers"]],
            ["2026.09.2", "2026.09.12"],
            "a lexicographic sort would put 2026.09.12 first",
        )

    def test_the_key_orders_across_month_and_patch(self):
        got = sorted(
            ["2026.10.1", "2026.09.20", "2026.09.3", "2026.10.0"],
            key=append_index.layer_key,
        )
        self.assertEqual(got, ["2026.09.3", "2026.09.20", "2026.10.0", "2026.10.1"])


class Idempotence(unittest.TestCase):
    def test_re_appending_the_same_layer_changes_nothing(self):
        """A deposit is re-runnable after a transient registry failure."""
        d = doc(counter=7, layers=[entry("2026.09.1")])
        same, changed = append_index.append(
            d, "2026.09.1", "sha256:" + "a" * 64, "rolling", 1, ISSUED
        )
        self.assertFalse(changed, "a no-op must report itself as one")
        self.assertEqual(same["counter"], 7, "the document counter must not climb")
        self.assertEqual(len(same["layers"]), 1, "the entry must not be duplicated")

    def test_a_real_append_climbs_the_document_counter_once(self):
        d = doc(counter=7, layers=[entry("2026.09.1")])
        d, changed = append_index.append(
            d, "2026.09.2", "sha256:" + "b" * 64, "rolling", 2, ISSUED
        )
        self.assertTrue(changed)
        self.assertEqual(d["counter"], 8)


class Refusals(unittest.TestCase):
    def test_one_layer_id_with_different_bytes_is_refused(self):
        """The index must not be rewritten to agree with a republish."""
        d = doc(layers=[entry("2026.09.1", "sha256:" + "a" * 64)])
        with self.assertRaises(SystemExit) as caught:
            append_index.append(
                d, "2026.09.1", "sha256:" + "f" * 64, "rolling", 1, ISSUED
            )
        msg = str(caught.exception)
        self.assertIn("2026.09.1", msg)
        self.assertIn("immutability", msg, "the refusal must say WHY")

    def test_a_digest_that_is_not_a_sha256_reference_is_refused(self):
        with self.assertRaises(SystemExit):
            append_index.append(doc(), "2026.09.1", "deadbeef", "rolling", 1, ISSUED)


class Shape(unittest.TestCase):
    def test_the_document_carries_exactly_the_fields_varve_parses(self):
        """`LineIndex` is `deny_unknown_fields`; a stray key fails to verify."""
        d, _ = append_index.append(doc(layers=[]), "2026.09.1",
                                   "sha256:" + "a" * 64, "rolling", 1, ISSUED)
        self.assertEqual(set(d), {"line", "counter", "issued-at", "layers"})
        self.assertEqual(set(d["layers"][0]), {"layer", "digest", "channel", "counter"})

    def test_a_round_trip_through_json_keeps_the_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "2026.09.json"
            d, _ = append_index.append(doc(layers=[]), "2026.09.1",
                                       "sha256:" + "a" * 64, "rolling", 1, ISSUED)
            p.write_text(json.dumps(d, indent=2) + "\n")
            self.assertEqual(json.loads(p.read_text()), d)


if __name__ == "__main__":
    unittest.main()
