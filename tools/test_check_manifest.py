#!/usr/bin/env python3
"""The checker's rules, on manifests that have gone wrong.

Its three defects, all live until 2026-09-18: it examined `tool` and `vsix`
only; it asked the forge for each entry's `version`, which is not a tag for a
hub payload; and its vsix-template check was written `"%V" in asset is False`,
which Python reads as `("%V" in asset) and (asset is False)` — never true, so
the check never ran at all.

Run: python3 -m unittest discover -s tools -p 'test_*.py'
"""
import importlib.util
import pathlib
import tomllib
import unittest

spec = importlib.util.spec_from_file_location(
    "check_manifest", pathlib.Path(__file__).parent / "check-manifest.py"
)
check_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_manifest)

BASE = '''
[varve]
version = "v0.36.0"

[[tool]]
name    = "varve-producer"
repo    = "pulseengine/varve"
version = "v0.36.0"

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
asset     = "varve-core-%V-rustdoc.tar.gz"
'''


class TheArrangementThisRealmUses(unittest.TestCase):
    def test_one_release_contributing_a_tool_a_crate_and_a_document_is_fine(self):
        # varve ships all three from v0.36.0. Keying the duplicate-pin check on
        # `version` made this look like pulseengine/varve pinned at both
        # "v0.36.0" and "0.36.0" — a false alarm on a correct manifest.
        self.assertEqual(check_manifest.problems(tomllib.loads(BASE)), [])

    def test_a_hub_payload_does_not_look_like_a_second_pin(self):
        # with-device asks for v0.7.2 and IS 0.2.2; only the tag is a pin.
        fail = check_manifest.problems(tomllib.loads(BASE))
        self.assertEqual([f for f in fail if "jess" in f], [])


class WhatItMustCatch(unittest.TestCase):
    def test_one_repo_at_two_releases(self):
        m = tomllib.loads(BASE + '''
[[tool]]
name    = "varve"
repo    = "pulseengine/varve"
version = "v0.35.0"
''')
        fail = check_manifest.problems(m)
        self.assertTrue(any("pinned at 2 releases" in f for f in fail), fail)
        self.assertTrue(any("v0.35.0" in f and "v0.36.0" in f for f in fail), fail)

    def test_a_vsix_template_with_no_version_placeholder(self):
        m = tomllib.loads(BASE.replace('asset   = "rivet-sdlc-%V.vsix"', 'asset   = "rivet-sdlc.vsix"'))
        fail = check_manifest.problems(m)
        self.assertTrue(any("has no %V" in f for f in fail), fail)

    def test_a_document_naming_a_payload_the_layer_does_not_carry(self):
        # varve deposit refuses this after a 20-minute fetch; here it is free.
        m = tomllib.loads(BASE.replace('documents = "varve-core"', 'documents = "varve_core"'))
        fail = check_manifest.problems(m)
        self.assertTrue(any("varve_core" in f for f in fail), fail)

    def test_a_document_cannot_document_another_document(self):
        m = tomllib.loads(BASE.replace('documents = "varve-core"', 'documents = "varve-core-api"'))
        fail = check_manifest.problems(m)
        self.assertTrue(any("varve-core-api" in f for f in fail), fail)


class CouldNotAsk(unittest.TestCase):
    """"I could not check" is not "it is not there"."""

    class Reply:
        def __init__(self, code, stderr=""):
            self.returncode, self.stderr, self.stdout = code, stderr, ""

    def run_returning(self, reply):
        def run(*_a, **_k):
            return reply
        return run

    def test_a_present_release_exists(self):
        state, _ = check_manifest.gh_release_state(
            "pulseengine/varve", "v0.36.0", run=self.run_returning(self.Reply(0))
        )
        self.assertEqual(state, "exists")

    def test_an_absent_release_is_missing(self):
        state, _ = check_manifest.gh_release_state(
            "pulseengine/varve", "v9.9.9",
            run=self.run_returning(self.Reply(1, "release not found")),
        )
        self.assertEqual(state, "missing")

    def test_no_token_is_UNKNOWN_and_not_missing(self):
        # The exact failure this gate produced on its first run: a job without
        # GH_TOKEN reported eleven correct pins as absent, including a release
        # published minutes earlier.
        state, detail = check_manifest.gh_release_state(
            "pulseengine/kiln", "v0.5.0",
            run=self.run_returning(self.Reply(
                4,
                "gh: To use GitHub CLI in a GitHub Actions workflow, set the GH_TOKEN "
                "environment variable.",
            )),
        )
        self.assertEqual(
            state, "unknown",
            "a job that cannot authenticate must not report the release as absent",
        )
        self.assertIn("GH_TOKEN", detail)

    def test_a_rate_limit_is_unknown_too(self):
        state, _ = check_manifest.gh_release_state(
            "pulseengine/spar", "v0.40.0",
            run=self.run_returning(self.Reply(1, "API rate limit exceeded")),
        )
        self.assertEqual(state, "unknown")


class TheLiveManifest(unittest.TestCase):
    def test_the_manifest_in_this_repository_passes_the_offline_checks(self):
        path = pathlib.Path(__file__).parent.parent / "layer.toml"
        with open(path, "rb") as f:
            m = tomllib.load(f)
        self.assertEqual(check_manifest.problems(m), [])


if __name__ == "__main__":
    unittest.main()
