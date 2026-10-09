#!/usr/bin/env python3
"""The scanner decides, without a person, what an immutable signed layer pins.

Two defects these tests exist for, both found on 2026-09-18 after the scanner
was merged and before its first cron tick:

  * it read only `[[tool]]`, so the `[[crate]]` and `[[docs]]` entries a layer
    had just started carrying would have frozen at 0.36.0 while varve-producer
    moved on, with nothing saying so;
  * it compared `version` against the latest tag, so `with-device` — payload
    0.2.2 from release v0.7.2 — was "moved" on every scan, and the rewrite
    would have signed it as version v0.7.2, a number that payload never had.

Run: python3 -m unittest discover -s tools -p 'test_*.py'
"""
import importlib.util
import pathlib
import tomllib
import unittest

spec = importlib.util.spec_from_file_location(
    "scan_pins", pathlib.Path(__file__).parent / "scan-pins.py"
)
scan_pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan_pins)

MANIFEST = '''
[varve]
version = "v0.36.0"

[realm]
name = "pulseengine"

[[tool]]
name    = "rivet"
version = "v0.37.0"

[[tool]]
name    = "spar"
version = "v0.37.0"

[[tool]]
name    = "with-device"
repo    = "pulseengine/jess"
version = "0.2.2"
release = "v0.7.2"

[[vsix]]
name    = "rivet-sdlc"
repo    = "pulseengine/rivet"
version = "v0.37.0"
asset   = "rivet-sdlc-%V.vsix"

[[crate]]
name    = "varve-core"
repo    = "pulseengine/varve"
version = "0.36.0"
release = "v0.36.0"

[[docs]]
name      = "varve-core-api"
repo      = "pulseengine/varve"
version   = "0.36.0"
release   = "v0.36.0"
documents = "varve-core"
'''


def manifest():
    return tomllib.loads(MANIFEST)


def fixed(**tags):
    """A `latest` that answers from a table, and refuses an unexpected repo."""
    def latest(repo):
        if repo not in tags:
            raise AssertionError(f"scanner asked about an unexpected repo: {repo}")
        return tags[repo]
    return latest


EDGES = """
[varve]
version = "v0.39.0"

[realm]
name    = "covalent"
channel = "rolling"

[[include]]
realm  = "pulseengine"
layer  = "2026.10.4"
digest = "sha256:aaaa"

[[include]]
realm  = "pulseengine-wasm"
layer  = "2026.09.0"
digest = "sha256:bbbb"
"""

MIXED = MANIFEST + """
[[include]]
realm  = "pulseengine"
layer  = "2026.10.4"
digest = "sha256:aaaa"
"""


def edges_manifest():
    return tomllib.loads(EDGES)


def lines(**table):
    """A `newer_layers` that answers from a table.

    A value is either a list of newer layer ids, or None for "that realm
    publishes no signed index for this line" — the state that must never read
    as "nothing moved".
    """
    def newer_layers(realm, layer):
        if realm not in table:
            raise AssertionError(f"asked about an unexpected realm: {realm}")
        answer = table[realm]
        if answer is None:
            return (False, [])
        # (layer, digest) pairs: an include needs both, and a helper that
        # yielded ids alone would let a half-proposal pass the tests.
        return (True, [(x, f"sha256:{x.replace('.', '')}") for x in answer])
    return newer_layers


class CompositionEdges(unittest.TestCase):
    """THE defect this realm shipped with: its manifest holds ONLY
    `[[include]]`, so every scan examined zero entries and printed "nothing
    moved" — through four layers of drift, while the composition pinned
    pulseengine 2026.10.4 and that line reached 2026.10.8.

    Not REWRITING an include is correct and stays correct: a digest pin exists
    so that moving a composition is a reviewed decision. Not LOOKING at one is
    what let the realm appear healthy while standing still.
    """

    def test_an_include_whose_line_moved_is_PROPOSED_with_its_digest(self):
        """A complete edit, not a description of one.

        This test used to assert the move landed in `needs_a_person` — a note
        telling someone to go and type a layer id and a digest by hand. That is
        the shape ruled out: a realm needing hand work per upstream move is a
        realm that stops being maintained. The move is PROPOSED now, and a
        pull request is the review the digest pin exists for.
        """
        updates, notes, edges = scan_pins.plan(
            edges_manifest(),
            fixed(),
            lines(**{"pulseengine": ["2026.10.5", "2026.10.8"],
                     "pulseengine-wasm": []}),
        )
        self.assertEqual(notes, [], "a derivable move is not a note")
        self.assertEqual(updates, [], "an include never enters the payload rewrite")
        self.assertEqual(len(edges), 1, edges)
        realm, old_layer, old_digest, new_layer, new_digest = edges[0]
        self.assertEqual(realm, "pulseengine")
        self.assertEqual(old_layer, "2026.10.4")
        # THE NEWEST, not the first one found.
        self.assertEqual(new_layer, "2026.10.8")
        # And the digest, which is the half that makes the pin a pin.
        self.assertEqual(new_digest, "sha256:2026108")
        self.assertNotEqual(old_digest, new_digest)

    def test_an_include_is_never_rewritten(self):
        updates, _, _edges = scan_pins.plan(
            edges_manifest(),
            fixed(),
            lines(**{"pulseengine": ["2026.10.8"],
                     "pulseengine-wasm": ["2026.10.0"]}),
        )
        self.assertEqual(updates, [], "an include reached the rewrite list")

    def test_an_include_at_the_newest_layer_says_nothing(self):
        _, notes, _edges = scan_pins.plan(
            edges_manifest(),
            fixed(),
            lines(**{"pulseengine": [], "pulseengine-wasm": []}),
        )
        self.assertEqual(notes, [])

    def test_a_broken_probe_is_an_error_not_a_claim_about_the_upstream(self):
        """THE one that was missing, and it cost a green run that did nothing.

        `varve outdated` exits 0 and says "cannot answer", carrying
        `answerable: false` so the two states are distinguishable. A NON-ZERO
        exit means the probe could not run at all.

        The first real scan pinned varve v0.39.0, where `outdated` does not
        exist — exit 2, "unrecognized subcommand". The scanner reported both
        covalent edges as "that realm publishes no signed line index", which is
        a false statement about pulseengine, and proposed nothing while looking
        healthy.

        So a checker that raises must propagate, not be absorbed into a note.
        """
        def broken(realm, layer):
            raise RuntimeError("`varve outdated` failed (exit 2)")

        with self.assertRaises(RuntimeError) as caught:
            scan_pins.plan(edges_manifest(), fixed(), broken)
        self.assertIn("exit 2", str(caught.exception))

    def test_an_unanswerable_include_is_reported_not_treated_as_unmoved(self):
        """A realm with no signed index cannot be asked, and that is a
        different fact from "nothing newer exists" — which is why
        `varve outdated` carries an `answerable` flag at all."""
        _, notes, _edges = scan_pins.plan(
            edges_manifest(),
            fixed(),
            lines(**{"pulseengine": [], "pulseengine-wasm": None}),
        )
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("CANNOT BE ESTABLISHED", notes[0])
        self.assertIn("pulseengine-wasm", notes[0])

    def test_includes_with_no_checker_refuse_rather_than_skip(self):
        """Scanning payloads only and calling it a scan is the original bug."""
        with self.assertRaises(RuntimeError) as caught:
            scan_pins.plan(edges_manifest(), fixed())
        self.assertIn("Refusing to scan", str(caught.exception))

    def test_payload_scanning_is_unaffected_by_edges(self):
        updates, notes, _edges = scan_pins.plan(
            tomllib.loads(MIXED),
            fixed(**{
                "pulseengine/rivet": "v0.38.0", "pulseengine/spar": "v0.37.0",
                "pulseengine/jess": "v0.7.2", "pulseengine/varve": "v0.36.0",
            }),
            lines(**{"pulseengine": ["2026.10.8"]}),
        )
        self.assertIn(("tool", "rivet", "version", "v0.37.0", "v0.38.0"), updates)
        self.assertEqual(len(_edges), 1, _edges)
        self.assertEqual(_edges[0][3], "2026.10.8")
        self.assertFalse(
            any(u[0] == "include" for u in updates), "an include was rewritten"
        )


class Sections(unittest.TestCase):
    def test_sections_come_from_the_manifest_not_from_a_list_here(self):
        # The bug: a hand-kept list. A section the manifest defines must be
        # scanned because it is THERE, not because someone remembered it.
        self.assertEqual(
            scan_pins.sections(manifest()), ["tool", "vsix", "crate", "docs"]
        )

    def test_a_section_nobody_has_thought_of_yet_is_scanned(self):
        m = tomllib.loads(MANIFEST + '\n[[widget]]\nname = "gizmo"\nversion = "v1.0.0"\n')
        self.assertIn("widget", scan_pins.sections(m))
        updates, _, _edges = scan_pins.plan(
            m,
            fixed(**{
                "pulseengine/rivet": "v0.37.0", "pulseengine/spar": "v0.37.0",
                "pulseengine/jess": "v0.7.2", "pulseengine/varve": "v0.36.0",
                "pulseengine/gizmo": "v2.0.0",
            }),
        )
        self.assertIn(("widget", "gizmo", "version", "v1.0.0", "v2.0.0"), updates)


class WhatMoved(unittest.TestCase):
    def test_nothing_moved_when_every_pin_is_current(self):
        updates, notes, _edges = scan_pins.plan(manifest(), fixed(**{
            "pulseengine/rivet": "v0.37.0", "pulseengine/spar": "v0.37.0",
            "pulseengine/jess": "v0.7.2", "pulseengine/varve": "v0.36.0",
        }))
        self.assertEqual(updates, [])
        self.assertEqual(notes, [])

    def test_a_hub_payload_is_compared_on_its_RELEASE_not_its_version(self):
        # 0.2.2 != v0.7.2 is not movement: the payload's number is its own.
        _, notes, _edges = scan_pins.plan(manifest(), fixed(**{
            "pulseengine/rivet": "v0.37.0", "pulseengine/spar": "v0.37.0",
            "pulseengine/jess": "v0.7.2", "pulseengine/varve": "v0.36.0",
        }))
        self.assertEqual(notes, [])

    def test_a_moved_hub_release_changes_nothing_and_asks_for_a_person(self):
        updates, notes, _edges = scan_pins.plan(manifest(), fixed(**{
            "pulseengine/rivet": "v0.37.0", "pulseengine/spar": "v0.37.0",
            "pulseengine/jess": "v0.8.0", "pulseengine/varve": "v0.36.0",
        }))
        self.assertEqual(
            [u for u in updates if u[1] == "with-device"], [],
            "the payload version cannot be derived from a tag; guessing it signs a lie",
        )
        self.assertEqual(len(notes), 1)
        self.assertIn("with-device", notes[0])
        self.assertIn("v0.8.0", notes[0])

    def test_a_crate_and_its_docs_move_in_both_fields(self):
        updates, notes, _edges = scan_pins.plan(manifest(), fixed(**{
            "pulseengine/rivet": "v0.37.0", "pulseengine/spar": "v0.37.0",
            "pulseengine/jess": "v0.7.2", "pulseengine/varve": "v0.37.0",
        }))
        self.assertEqual(notes, [])
        # Both fields, because the asset name is built from the bare version
        # and the fetch from the tag: moving one alone fetches a release whose
        # asset the manifest does not name.
        self.assertIn(("crate", "varve-core", "release", "v0.36.0", "v0.37.0"), updates)
        self.assertIn(("crate", "varve-core", "version", "0.36.0", "0.37.0"), updates)
        self.assertIn(("docs", "varve-core-api", "release", "v0.36.0", "v0.37.0"), updates)
        self.assertIn(("docs", "varve-core-api", "version", "0.36.0", "0.37.0"), updates)

    def test_a_tool_and_a_vsix_move_on_version(self):
        updates, _, _edges = scan_pins.plan(manifest(), fixed(**{
            "pulseengine/rivet": "v0.38.0", "pulseengine/spar": "v0.37.0",
            "pulseengine/jess": "v0.7.2", "pulseengine/varve": "v0.36.0",
        }))
        self.assertIn(("tool", "rivet", "version", "v0.37.0", "v0.38.0"), updates)
        self.assertIn(("vsix", "rivet-sdlc", "version", "v0.37.0", "v0.38.0"), updates)
        # spar shares rivet's pinned version and did NOT move.
        self.assertEqual([u for u in updates if u[1] == "spar"], [])


class Rewriting(unittest.TestCase):
    def test_only_the_named_entry_moves_though_two_share_a_version(self):
        out = scan_pins.apply_updates(
            MANIFEST, [("tool", "rivet", "version", "v0.37.0", "v0.38.0")]
        )
        m = tomllib.loads(out)
        by = {t["name"]: t["version"] for t in m["tool"]}
        self.assertEqual(by["rivet"], "v0.38.0")
        self.assertEqual(by["spar"], "v0.37.0", "a global replace moved a bystander")
        # The vsix of the same name is its own entry in its own section.
        self.assertEqual(m["vsix"][0]["version"], "v0.37.0")

    def test_the_entry_that_moves_is_not_the_first_one_sharing_that_version(self):
        # rivet comes FIRST in the file and shares spar's version, so a rewrite
        # that is not anchored to the name gets the right answer by luck when
        # the target happens to be first. Move the SECOND one.
        out = scan_pins.apply_updates(
            MANIFEST, [("tool", "spar", "version", "v0.37.0", "v0.41.0")]
        )
        by = {t["name"]: t["version"] for t in tomllib.loads(out)["tool"]}
        self.assertEqual(by["spar"], "v0.41.0")
        self.assertEqual(by["rivet"], "v0.37.0", "an unanchored rewrite moved rivet instead")

    def test_a_version_in_a_later_section_is_not_taken_for_an_earlier_one(self):
        # The crate's bare 0.36.0 also appears on the docs entry; each must be
        # rewritten in its own section.
        out = scan_pins.apply_updates(
            MANIFEST, [("docs", "varve-core-api", "version", "0.36.0", "0.37.0")]
        )
        m = tomllib.loads(out)
        self.assertEqual(m["docs"][0]["version"], "0.37.0")
        self.assertEqual(m["crate"][0]["version"], "0.36.0", "the crate was moved instead")

    def test_a_rewrite_that_matches_nothing_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(AssertionError):
            scan_pins.apply_updates(
                MANIFEST, [("tool", "rivet", "version", "v9.9.9", "v10.0.0")]
            )

    def test_the_crate_and_the_docs_are_rewritten_in_their_own_sections(self):
        out = scan_pins.apply_updates(MANIFEST, [
            ("crate", "varve-core", "release", "v0.36.0", "v0.37.0"),
            ("crate", "varve-core", "version", "0.36.0", "0.37.0"),
            ("docs", "varve-core-api", "release", "v0.36.0", "v0.37.0"),
            ("docs", "varve-core-api", "version", "0.36.0", "0.37.0"),
        ])
        m = tomllib.loads(out)
        self.assertEqual(m["crate"][0], {
            "name": "varve-core", "repo": "pulseengine/varve",
            "version": "0.37.0", "release": "v0.37.0",
        })
        self.assertEqual(m["docs"][0]["version"], "0.37.0")
        self.assertEqual(m["docs"][0]["release"], "v0.37.0")
        self.assertEqual(m["docs"][0]["documents"], "varve-core")


class TheRealManifest(unittest.TestCase):
    def test_every_entry_in_the_live_manifest_is_classified(self):
        # Not a fixture: the file this scanner actually rewrites. A pin shape
        # nobody anticipated shows up here first.
        path = pathlib.Path(__file__).parent.parent / "layer.toml"
        with open(path, "rb") as f:
            m = tomllib.load(f)
        for section in scan_pins.sections(m):
            for entry in m[section]:
                self.assertIn("name", entry, f"{section} entry without a name")
                self.assertIn("version", entry, f"{section} '{entry.get('name')}' has no version")
                # tracks_tag must answer for every entry without raising.
                scan_pins.tracks_tag(entry)

    def test_every_live_entry_is_classified_as_tracking_its_tag_or_not(self):
        # pulseengine-layers asserts here that its known hub payload
        # (with-device, 0.2.2 from release v0.7.2) is still recognised as one.
        # This realm carries no hub today, so asserting one exists would be a
        # test of nothing. What must hold either way: every entry answers, and
        # any entry that does NOT track its tag is one the scanner will refuse
        # to bump on its own — which is a fact worth printing rather than
        # discovering during an unattended deposit.
        path = pathlib.Path(__file__).parent.parent / "layer.toml"
        with open(path, "rb") as f:
            m = tomllib.load(f)
        independent = [
            e["name"] for s in scan_pins.sections(m) for e in m[s]
            if not scan_pins.tracks_tag(e)
        ]
        self.assertEqual(
            independent, [],
            f"these payloads' versions are not their release tags, so the scanner "
            f"will report them for a person rather than bump them: {independent}",
        )


if __name__ == "__main__":
    unittest.main()
