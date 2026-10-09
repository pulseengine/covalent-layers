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
from pins import asked_tag, includes, repo_of, sections, tracks_tag  # noqa: E402

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


def varve_newer_layers(varve_bin: str, realms_file: str, channel: str) -> "callable":
    """Ask VARVE whether a realm's line holds anything newer than `layer`.

    Deliberately not a line index parsed here. The index is a DSSE envelope
    signed by the realm's root, and verifying it correctly — right key, right
    line, counter not regressing, omission detected — is varve's job and is
    system-tested there. A copy of that logic in this repository would be a
    second implementation of a trust decision, which is the thing this realm
    exists to avoid (REQ-PEL-ASSEMBLER-001). A tag listing is not an option
    either: a registry that HIDES a layer is undetectable that way.

    So: synthesise the pin varve already knows how to answer, and run
    `varve outdated --json`, whose `answerable` flag exists for exactly this —
    telling "there is nothing newer" apart from "I cannot know".

    The channel comes from the COMPOSING realm, because a composition on the
    rolling channel includes rolling layers. If that is ever wrong, varve
    answers "cannot answer" and the caller reports it; it cannot silently
    answer about the wrong line.
    """
    import shutil
    import tempfile

    def ask(realm: str, layer: str) -> tuple[bool, list[str]]:
        work = tempfile.mkdtemp(prefix="covalent-include-")
        try:
            shutil.copyfile(realms_file, os.path.join(work, "varve-realms.toml"))
            with open(os.path.join(work, "varve.toml"), "w") as f:
                f.write(
                    "manifest-version = 1\n\n[toolchain]\n"
                    f'realm   = "{realm}"\n'
                    f'channel = "{channel}"\n'
                    f'layer   = "{layer}"\n'
                )
            out = subprocess.run(
                [os.path.abspath(varve_bin), "outdated", "--json"],
                cwd=work, capture_output=True, text=True,
                env={**os.environ, "VARVE_STORE": os.path.join(work, "store")},
            )
            if out.returncode != 0 or not out.stdout.strip():
                # Not an error here: varve exits non-zero when it cannot
                # answer, and "cannot answer" is a reportable state, not a
                # crash. The caller turns it into a note for a person.
                return (False, [])
            answer = json.loads(out.stdout)
            if not answer.get("answerable"):
                return (False, [])
            # THE DIGEST TOO. An `[[include]]` names a layer by the digest of
            # its signed manifest, so a layer id alone is half a proposal —
            # and the half that is missing is the one that makes the pin a
            # pin. varve already returns it; dropping it here is what made
            # this a report rather than a change to approve.
            return (
                True,
                [(n["layer"], n["digest"]) for n in answer.get("newer", [])],
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return ask


def plan(
    manifest: dict, latest: "callable", newer_layers: "callable" = None
) -> tuple[list[tuple], list[str]]:
    """What to rewrite, and what needs a person.

    `latest(repo)` returns the newest tag for a payload's repository.
    `newer_layers(realm, layer)` answers, for a composition edge, whether that
    realm's line holds anything newer: `(answerable, [newer layer ids])`. Pure
    apart from those two, so every rule here is testable without a network.

    A manifest with includes and no `newer_layers` RAISES rather than skipping
    them. Skipping is what this scanner did for four layers of drift, and the
    whole module is built on "I could not ask" never looking like "nothing
    moved".
    """
    updates: list[tuple] = []
    needs_a_person: list[str] = []
    # Composition edges that moved, as (realm, old_layer, old_digest,
    # new_layer, new_digest). Kept separate from `updates` so the payload
    # rewriter cannot touch one by accident.
    include_updates: list[tuple] = []
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

    # COMPOSITION EDGES. Reported, never rewritten (pins.NOT_PAYLOADS says
    # why): moving a composition to a newer upstream layer decides whose bytes
    # this realm vouches for, and a digest pin exists precisely so that is not
    # an unattended rewrite. But it must be SAID, or a manifest that holds only
    # includes — like this one — reports "nothing moved" forever while the
    # upstream line runs away from it.
    edges = includes(manifest)
    if edges and newer_layers is None:
        raise RuntimeError(
            "this manifest declares composition edges and no way to ask whether "
            "they moved. Refusing to scan: reporting 'nothing moved' without "
            "having looked is the failure this scanner is built to prevent."
        )
    for edge in edges:
        realm, layer = edge["realm"], edge["layer"]
        answerable, newer = newer_layers(realm, layer)
        if not answerable:
            needs_a_person.append(
                f"include of realm '{realm}' pins layer {layer}, and whether "
                f"anything newer exists CANNOT BE ESTABLISHED — that realm "
                f"publishes no signed line index for this line. A registry tag "
                f"listing would answer faster and is refused: a host that hides "
                f"a layer serves nothing that fails verification, so a listing "
                f"cannot tell 'there is nothing newer' from 'I am not telling "
                f"you'. This is not 'nothing moved'."
            )
            continue
        if not newer:
            continue
        # THE NEWEST, with its digest — a complete edit, not a description of
        # one. Moving a composition remains a REVIEWED decision (see
        # pins.NOT_PAYLOADS): this proposes it, and a pull request is the
        # review. What was wrong was equating "reviewed" with "typed by hand".
        newest_layer, newest_digest = newer[-1]
        include_updates.append(
            (realm, layer, edge["digest"], newest_layer, newest_digest)
        )
    return updates, needs_a_person, include_updates


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


def apply_include_updates(text: str, updates: list[tuple]) -> str:
    """Move a composition edge: its layer id AND its digest, together.

    Anchored on the realm name and on BOTH current values, so a half-applied
    move is impossible. That matters more here than for a payload: an
    `[[include]]` whose layer says one thing and whose digest says another is
    not a stale pin, it is a manifest that names bytes nobody chose — and
    varve would fetch the digest while a reader believes the layer id.

    Each rewrite must match exactly once or this raises.
    """
    for realm, old_layer, old_digest, new_layer, new_digest in updates:
        # The block for THIS realm, whatever order its keys appear in.
        block = re.compile(
            r'(\[\[include\]\][^\[]*?realm\s*=\s*"' + re.escape(realm) + r'"[^\[]*?)(?=\[\[|\Z)',
            re.S,
        )
        m = block.search(text)
        if m is None:
            raise AssertionError(f"no [[include]] block for realm {realm!r}")
        body = m.group(1)
        for field, old, new in (
            ("layer", old_layer, new_layer),
            ("digest", old_digest, new_digest),
        ):
            pat = re.compile(
                r'(' + re.escape(field) + r'\s*=\s*")' + re.escape(old) + r'(")'
            )
            body, n = pat.subn(r"\g<1>" + new + r"\g<2>", body, count=1)
            if n != 1:
                raise AssertionError(
                    f"could not rewrite include {realm!r} {field} "
                    f"({old} -> {new}) — matched {n} times, expected exactly 1"
                )
        text = text[: m.start(1)] + body + text[m.end(1) :]
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

    # The include checker, when this manifest has composition edges. varve and
    # the realms file must be on hand; without them the scan REFUSES rather
    # than silently examining payloads only.
    newer_layers = None
    if manifest.get("include"):
        varve_bin = os.environ.get("VARVE_BIN", "")
        realms = os.environ.get("VARVE_REALMS", "")
        if not varve_bin or not os.path.exists(varve_bin):
            print(
                "::error::this manifest declares composition edges, and VARVE_BIN "
                "is unset or missing. Whether an include has moved is answerable "
                "only from the included realm's SIGNED line index, which varve "
                "verifies. Refusing to scan payloads alone and call it a scan.",
                file=sys.stderr,
            )
            return 1
        if not realms or not os.path.exists(realms):
            print(
                "::error::VARVE_REALMS is unset or missing. The realms file carries "
                "the trust root an included realm's index is verified against; "
                "without it varve cannot answer and this scan would under-report.",
                file=sys.stderr,
            )
            return 1
        newer_layers = varve_newer_layers(
            varve_bin, realms, manifest["realm"]["channel"]
        )

    updates, needs_a_person, include_updates = [], [], []
    try:
        updates, needs_a_person, include_updates = plan(manifest, latest, newer_layers)
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

    if apply and (updates or include_updates):
        with open(MANIFEST) as f:
            text = f.read()
        text = apply_updates(text, updates)
        text = apply_include_updates(text, include_updates)
        with open(MANIFEST, "w") as f:
            f.write(text)
        # Re-read with the real parser: a rewrite that produced something the
        # assembler cannot read would fail later, in the signing half.
        with open(MANIFEST, "rb") as f:
            again = tomllib.load(f)
        for section, name, field, _old, new in updates:
            entry = next(e for e in again[section] if e["name"] == name)
            assert entry[field] == new, f"{section} '{name}' {field} did not take"
        for realm, _ol, _od, new_layer, new_digest in include_updates:
            edge = next(e for e in again.get("include", []) if e["realm"] == realm)
            assert edge["layer"] == new_layer, f"include {realm} layer did not take"
            assert edge["digest"] == new_digest, f"include {realm} digest did not take"

    for row in updates:
        print("\t".join(row))
    for realm, old_layer, _od, new_layer, _nd in include_updates:
        print("\t".join(("include", realm, "layer", old_layer, new_layer)))
    print(
        f"{len(updates)} pin(s) moved, {len(include_updates)} composition "
        f"edge(s) moved, {len(needs_a_person)} needing a person "
        f"({len(manifest.get('include', []))} edge(s) examined)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
