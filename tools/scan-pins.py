#!/usr/bin/env python3
"""Which pinned payloads have a newer upstream release, and the rewrite for them.

Reads layer.toml — the realm's single source of truth — asks each payload's
repository for its latest release tag, and (with --apply) rewrites the pins that
moved. Prints one `section<TAB>name<TAB>field<TAB>old<TAB>new` line per change.

Deliberately NOT a diff of the workflow file: pins live in layer.toml, and a
scanner that read them from anywhere else would be a second place the realm is
defined (varve REQ-PEL-MANIFEST-001). For the same reason the REWRITE lives
here rather than in the workflow: one reader and one writer of layer.toml, or
the two drift and the drift is only visible in a signed layer.

EVERY SECTION, DERIVED FROM THE MANIFEST. The sections are whatever top-level
arrays layer.toml defines — `tool`, `vsix`, `crate`, `docs` today — never a list
written here. A hand-kept list is how `[[crate]]` and `[[docs]]` came to be
carried by a layer and ignored by the scanner: their pins would have frozen at
0.36.0 while varve-producer moved on, and nothing would have said so.

THE TAG AN ENTRY ASKS FOR IS NOT ALWAYS ITS VERSION. A hub release ships a
payload under its own number: pulseengine/jess tags `v0.7.2` and ships
`with-device` at `0.2.2` (varve REQ-PAYLOADID-001). Comparing `version` against
the latest tag reported that payload as moved on every single scan, and the
rewrite would have set its version to `v0.7.2` — a number that payload does not
have, signed into the layer as though it did. So:

  * no `release` key        — the version IS the tag; compare and bump `version`.
  * `release`, and `version` is that tag with the leading `v` off — both track
    the tag; compare `release` and bump both.
  * `release`, and `version` independent of it (the hub case) — compare
    `release`, and if it moved, REPORT IT AND CHANGE NOTHING: the payload's own
    version cannot be derived from a tag, and guessing it would put a false
    claim inside the signature. A person states it.

Fail-loud by design. This feeds an AUTONOMOUS deposit, so "I could not ask" must
never look like "nothing moved" — a scanner that silently reported no movement
would freeze the realm while appearing healthy. Any repository that cannot be
queried is an error, and the caller stops.
"""
import json
import os
import re
import subprocess
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pins import asked_tag, repo_of, sections, tracks_tag  # noqa: E402

MANIFEST = "layer.toml"


def latest_release(repo: str) -> str:
    """The repository's latest release tag, or raise."""
    out = subprocess.run(
        ["gh", "release", "view", "--repo", repo, "--json", "tagName"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"{repo}: {out.stderr.strip()[:160]}")
    tag = json.loads(out.stdout)["tagName"]
    if not tag:
        raise RuntimeError(f"{repo}: latest release has an empty tag")
    return tag


def plan(manifest: dict, latest: "callable") -> tuple[list[tuple], list[str]]:
    """What to rewrite, and what needs a person.

    `latest(repo)` returns the newest tag. Pure apart from that, so the rules
    above are testable without a network.
    """
    updates: list[tuple] = []
    needs_a_person: list[str] = []
    for section in sections(manifest):
        for entry in manifest[section]:
            name = entry["name"]
            repo = repo_of(entry)
            release = entry.get("release")
            asked = asked_tag(entry)
            newest = latest(repo)
            if newest == asked:
                continue
            if release is not None and not tracks_tag(entry):
                needs_a_person.append(
                    f"{section} '{name}' asks for release {asked} and upstream is now "
                    f"{newest}, but its payload version {entry['version']} is its own "
                    f"number, not the tag. Only a person can say what the payload is at "
                    f"{newest} — bumping the tag alone would fetch a release whose asset "
                    f"this manifest does not name, and deriving the version from the tag "
                    f"would sign a claim the payload does not make."
                )
                continue
            if release is not None:
                updates.append((section, name, "release", release, newest))
                bare = newest.lstrip("v")
                if entry["version"] != bare:
                    updates.append((section, name, "version", entry["version"], bare))
            else:
                updates.append((section, name, "version", entry["version"], newest))
    return updates, needs_a_person


def apply_updates(text: str, updates: list[tuple]) -> str:
    """Rewrite each named pin in place.

    Anchored to the SECTION and the entry's name, never to a bare version
    string: several payloads share a version, and a global replace would move
    all of them. Each rewrite must match exactly once or this raises — a
    silently-unapplied bump would deposit a layer that disagrees with the file
    the deposit was derived from.
    """
    for section, name, field, old, new in updates:
        pattern = re.compile(
            r'(\[\[' + re.escape(section) + r'\]\][^\[]*?name\s*=\s*"' + re.escape(name)
            + r'"[^\[]*?' + re.escape(field) + r'\s*=\s*")' + re.escape(old) + r'(")',
            re.S,
        )
        text, n = pattern.subn(r"\g<1>" + new + r"\g<2>", text, count=1)
        if n != 1:
            raise AssertionError(
                f"could not rewrite {section} '{name}' {field} ({old} -> {new}) — "
                f"matched {n} times, expected exactly 1"
            )
    return text


def main(argv: list[str]) -> int:
    apply = "--apply" in argv[1:]
    with open(MANIFEST, "rb") as f:
        manifest = tomllib.load(f)
    failures: list[str] = []

    cache: dict[str, str] = {}

    def latest(repo: str) -> str:
        # One query per REPOSITORY: several payloads can come from one repo
        # (varve ships varve-producer, the crate and the rustdoc), and asking
        # again would multiply the rate-limit cost of every scan for no new
        # information.
        if repo not in cache:
            try:
                cache[repo] = latest_release(repo)
            except Exception as e:  # noqa: BLE001 — every failure is the same answer: stop
                failures.append(str(e))
                cache[repo] = None
        if cache[repo] is None:
            raise RuntimeError(f"{repo}: already failed")
        return cache[repo]

    updates, needs_a_person = [], []
    try:
        updates, needs_a_person = plan(manifest, latest)
    except RuntimeError:
        pass  # a failed query; reported below, and nothing is trusted

    if failures:
        for f in dict.fromkeys(failures):
            print(f"::error::could not ask upstream: {f}", file=sys.stderr)
        print(
            "::error::refusing to report movement from an incomplete scan — "
            "'I could not ask' is not 'nothing moved', and an autonomous "
            "depositor acting on the difference would freeze the realm while "
            "looking healthy",
            file=sys.stderr,
        )
        return 1

    for note in needs_a_person:
        print(f"::warning::{note}", file=sys.stderr)

    if apply and updates:
        with open(MANIFEST) as f:
            text = f.read()
        with open(MANIFEST, "w") as f:
            f.write(apply_updates(text, updates))
        # Re-read with the real parser: a rewrite that produced something the
        # assembler cannot read would fail later, in the signing half.
        with open(MANIFEST, "rb") as f:
            again = tomllib.load(f)
        for section, name, field, _old, new in updates:
            entry = next(e for e in again[section] if e["name"] == name)
            assert entry[field] == new, f"{section} '{name}' {field} did not take"

    for row in updates:
        print("\t".join(row))
    print(
        f"{len(updates)} pin(s) moved, {len(needs_a_person)} needing a person",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
