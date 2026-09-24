#!/usr/bin/env python3
"""Which registry is this realm's? Read from the manifest, never defaulted.

`next-layer-id.sh` defaulted to a literal `ghcr.io/pulseengine/layers`. That is
correct in exactly one repository. Copied into this one it read the FIRST
realm's published record and proposed layer 2026.09.6 counter 7 for a realm
whose registry is empty and whose first layer is counter 1 — and the counter is
the per-line anti-rollback high-water mark, so the layer would have carried a
claim about a history it does not have.

Run: python3 -m unittest discover -s tools -p 'test_*.py'
"""
import importlib.util
import os
import pathlib
import subprocess
import tomllib
import unittest

spec = importlib.util.spec_from_file_location("pins", pathlib.Path(__file__).parent / "pins.py")
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

ROOT = pathlib.Path(__file__).parent.parent


class TagDecoratesTheVersion(unittest.TestCase):
    """A tag that decorates the version is still derivable; a hub is not."""

    def test_a_decorated_tag_still_tracks(self):
        # binaryen: release `version_133` carries version 133, so a scanner
        # that sees `version_134` can bump this on its own.
        self.assertTrue(
            pins.tracks_tag({"name": "binaryen", "version": "133",
                             "release": "version_133"})
        )

    def test_a_hub_release_does_not_track(self):
        # with-device is 0.2.2 inside release v0.7.2 — unrelated numbers.
        self.assertFalse(
            pins.tracks_tag({"name": "with-device", "version": "0.2.2",
                             "release": "v0.7.2"})
        )

    def test_a_shared_suffix_is_not_a_derivation(self):
        # The trap the digit check exists for: v1.2.10 must NOT be read as
        # "version 10 decorated with v1.2.".
        self.assertFalse(
            pins.tracks_tag({"name": "trap", "version": "10",
                             "release": "v1.2.10"})
        )


class RegistryRef(unittest.TestCase):
    def test_the_scheme_is_stripped(self):
        self.assertEqual(
            pins.registry_ref({"realm": {"registry": "oci://ghcr.io/pulseengine/wasm-layers"}}),
            "ghcr.io/pulseengine/wasm-layers",
        )

    def test_a_trailing_slash_does_not_become_part_of_the_repository(self):
        self.assertEqual(
            pins.registry_ref({"realm": {"registry": "oci://ghcr.io/org/repo/"}}),
            "ghcr.io/org/repo",
        )

    def test_a_registry_that_is_not_oci_is_refused_rather_than_guessed(self):
        for bad in ["https://ghcr.io/org/repo", "ghcr.io/org/repo", ""]:
            with self.assertRaises(ValueError, msg=bad):
                pins.registry_ref({"realm": {"registry": bad}})

    def test_a_host_with_no_repository_is_refused(self):
        with self.assertRaises(ValueError):
            pins.registry_ref({"realm": {"registry": "oci://ghcr.io"}})

    def test_this_repository_resolves_to_its_own_registry(self):
        with open(ROOT / "layer.toml", "rb") as f:
            m = tomllib.load(f)
        ref = pins.registry_ref(m)
        self.assertTrue(ref.endswith("/wasm-layers"), ref)
        self.assertNotIn(
            "pulseengine/layers", ref,
            "this realm would derive its layer id from the pulseengine realm's record",
        )


class TheScriptUsesIt(unittest.TestCase):
    def test_no_registry_is_hardcoded_in_the_derivation_script(self):
        s = (ROOT / "tools" / "next-layer-id.sh").read_text()
        body = s.split("set -euo pipefail", 1)[1]
        self.assertNotIn(
            'ghcr.io/pulseengine/layers"', body,
            "a hardcoded registry default is right in one repository and silently wrong "
            "in every other",
        )
        self.assertIn("registry_ref", body, "the script does not read the manifest")

    def test_the_script_reports_this_realms_registry(self):
        # Runs the real script far enough to print its trace, with no token and
        # a registry that cannot be reached: what matters is WHICH ref it names.
        # The caller's environment, minus any override: a trimmed PATH picks up
        # whatever python3 is first, and an older one has no tomllib — which
        # fails the test for a reason that has nothing to do with the script.
        env = dict(os.environ, VARVE_REGISTRY_REF="")
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        r = subprocess.run(
            ["bash", "tools/next-layer-id.sh"],
            cwd=ROOT, capture_output=True, text=True, env=env,
        )
        out = r.stdout + r.stderr
        self.assertIn("wasm-layers", out, out[:400])
        self.assertNotIn("pulseengine/layers ", out, out[:400])


class WhatTheRegistryAnswers(unittest.TestCase):
    """404 NAME_UNKNOWN is an empty realm; every other failure is a refusal.

    A new realm has no registry repository, so listing its tags 404s — and
    refusing that would mean a realm could never receive its first layer. But a
    typo in the registry, a token without pull rights and an outage would all
    look the same as an empty realm, and the answer would then be layer .0
    counter 1 deposited on top of a line that already exists. varve has no
    revocation, so that is unrecoverable.
    """

    def run_with_curl(self, script: str):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            shim = pathlib.Path(d) / "curl"
            shim.write_text(script)
            shim.chmod(0o755)
            env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}",
                       VARVE_REGISTRY_REF="", GH_TOKEN="stub-token", VARVE_LAYER_LINE="2026.09")
            return subprocess.run(
                ["bash", "tools/next-layer-id.sh"],
                cwd=ROOT, capture_output=True, text=True, env=env,
            )

    def test_an_absent_repository_is_this_realms_first_layer(self):
        r = self.run_with_curl(
            '#!/bin/sh\n'
            'printf \'{"errors":[{"code":"NAME_UNKNOWN"}]}\\n404\'\n'
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().split("\t"), ["2026.09.0", "1"], r.stderr)

    def test_a_404_without_name_unknown_is_refused(self):
        r = self.run_with_curl('#!/bin/sh\nprintf \'not found\\n404\'\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn("NAME_UNKNOWN", r.stderr)

    def test_an_unauthorised_answer_is_refused_not_read_as_empty(self):
        # The dangerous one: a token without pull rights must never yield
        # "nothing is published here".
        r = self.run_with_curl('#!/bin/sh\nprintf \'{"errors":[{"code":"UNAUTHORIZED"}]}\\n401\'\n')
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("2026.09.0", r.stdout)
        self.assertIn("401", r.stderr)

    def test_a_server_error_is_refused(self):
        r = self.run_with_curl('#!/bin/sh\nprintf \'oops\\n503\'\n')
        self.assertEqual(r.returncode, 1)
        self.assertIn("503", r.stderr)


if __name__ == "__main__":
    unittest.main()
