#!/usr/bin/env python3
"""Check layer.toml against reality, before anything is signed.

Not a formatter. Every check here corresponds to a way a layer has actually
gone wrong in varve, or would silently go wrong:

  * a repo pinned at two RELEASES — one release's assets get checked against
    another release's sums; verification that passes while proving nothing
    (varve's assembler refuses this, and finding it here is cheaper)
  * a tag that does not exist upstream — a typo that fails 20 minutes into a
    deposit rather than in review
  * a per-platform vsix template with no %V — every platform resolves to one name
  * a document naming a payload the layer does not carry — `varve deposit`
    refuses it, and refusing here is cheaper than refusing after a 20-minute
    fetch

EVERY SECTION, AND THE RIGHT TAG. This checked `tool` and `vsix` only, so the
`[[crate]]` and `[[docs]]` entries a layer now carries went unchecked; and it
asked the forge for each entry's `version`, which for the hub payload
`with-device` (0.2.2 from release v0.7.2) is not a tag at all — so it reported
`pulseengine/jess@0.2.2 does not exist upstream` against a correct manifest.
Both rules now live in tools/pins.py, once.
"""
import os
import subprocess
import sys
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pins import asked_tag, entries, repo_of, sections  # noqa: E402


def gh_release_state(repo: str, tag: str, run=subprocess.run) -> tuple[str, str]:
    """("exists"|"missing"|"unknown", detail) for one tag.

    THREE answers, not two. `gh release view` exits non-zero both when the
    release is not there and when it could not ask — no token in the job, rate
    limit, network — and reading the second as the first is how this gate
    reported eleven correct pins as missing, `pulseengine/varve@v0.36.0`
    among them, in a job that simply had no GH_TOKEN. "I could not check" is
    its own verdict and has to be said in its own words, which is the same rule
    scan-pins.py already follows for movement.
    """
    r = run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "tagName"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return "exists", ""
    err = (r.stderr or "").strip()
    # gh says "release not found" for an absent tag, and something about
    # authentication, rate limits or the network for everything else.
    if "release not found" in err.lower() or "not found" in err.lower():
        return "missing", err[:160]
    return "unknown", err[:160]


def problems(manifest: dict) -> list[str]:
    """Everything wrong with the manifest that can be seen without a network."""
    fail: list[str] = []
    pins: dict[str, set[str]] = {}

    for section, e in entries(manifest):
        name = e["name"]
        # Keyed on the TAG, not the version: varve is pinned once as v0.36.0
        # and contributes a tool, a crate and a document from that one release.
        # Keying on `version` made that look like two pins of one repo — a
        # false alarm on exactly the arrangement this realm now uses.
        pins.setdefault(repo_of(e), set()).add(asked_tag(e))
        if section == "vsix":
            asset = e.get("asset", "")
            # `"%V" in asset is False` chains into `(...) and (asset is False)`
            # and can never be true, so this check was dead for its whole life.
            if "%V" not in asset:
                fail.append(f"vsix {name}: asset template {asset!r} has no %V")

    # One repo, one release. This is the dangerous one.
    for repo, tags in sorted(pins.items()):
        if len(tags) > 1:
            fail.append(
                f"{repo} is pinned at {len(tags)} releases ({', '.join(sorted(tags))}) — "
                f"one release's assets would be checked against another's sums"
            )

    # A document must name a payload the layer carries, and not a document:
    # `varve deposit` refuses otherwise, after the fetch.
    documentable = {
        e["name"] for s, e in entries(manifest) if s != "docs"
    }
    for _s, e in [(s, e) for s, e in entries(manifest) if s == "docs"]:
        target = e.get("documents")
        if target is not None and target not in documentable:
            fail.append(
                f"docs {e['name']}: documents {target!r}, which this layer does not carry "
                f"as a non-docs payload (it carries {', '.join(sorted(documentable))})"
            )
    return fail


def main() -> int:
    with open("layer.toml", "rb") as f:
        d = tomllib.load(f)
    fail = problems(d)

    unknown: list[str] = []
    if "--offline" not in sys.argv:
        asked: dict[str, set[str]] = {}
        for _s, e in entries(d):
            asked.setdefault(repo_of(e), set()).add(asked_tag(e))
        for repo, tags in sorted(asked.items()):
            for t in sorted(tags):
                state, detail = gh_release_state(repo, t)
                if state == "missing":
                    fail.append(f"{repo}@{t} does not exist upstream")
                elif state == "unknown":
                    unknown.append(f"{repo}@{t}: {detail}")

    if unknown:
        for u in unknown:
            print(f"::error::could not ask upstream: {u}", file=sys.stderr)
        print(
            "::error::refusing to call these pins good OR bad from an incomplete "
            "check — 'I could not ask' is not 'it is not there'. Give the job a "
            "GH_TOKEN, or run with --offline to skip the upstream check entirely.",
            file=sys.stderr,
        )

    for f in fail:
        print(f"FAIL: {f}", file=sys.stderr)
    if fail or unknown:
        return 1
    counts = ", ".join(f"{len(d[s])} {s}" for s in sections(d))
    print(f"layer.toml OK — {counts}; no repo at two releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
