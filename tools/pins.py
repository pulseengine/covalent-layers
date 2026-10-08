#!/usr/bin/env python3
"""What layer.toml pins, and which tag each pin asks for.

THE one answer to those two questions. They were answered separately in three
places — the scanner, the scanner's inline rewrite, and check-manifest.py — and
all three got the hub case wrong in the same way, because each re-derived the
rule from the field names rather than from what the fields MEAN.

Two rules, and the second is the one that keeps being missed:

  * The sections are whatever top-level arrays the manifest defines. Never a
    list written in code: `[[crate]]` and `[[docs]]` were carried by a layer
    and ignored by every tool here, so their pins would have frozen while the
    rest of the realm moved on, silently.

  * `version` is what the payload IS; the tag to ASK a forge for is `release`
    when the entry has one. A hub ships a payload under its own number —
    pulseengine/jess tags `v0.7.2` and ships `with-device` at `0.2.2` (varve
    REQ-PAYLOADID-001) — so reading `version` as a tag makes that payload
    permanently "moved" for the scanner and permanently "missing upstream" for
    the checker. Both were live.
"""


#: Top-level arrays that are NOT payload sections.
#:
#: `[[include]]` is a composition EDGE, not a payload: it names another
#: realm's layer by the digest of its signed manifest, and it has no `name`,
#: no `version` and no repository to ask for a newer release. A scanner that
#: treated it as a payload would fail on the missing name — and if it somehow
#: got past that, "bumping" an include is the one thing a digest pin exists to
#: prevent. Moving a composition to a newer upstream layer is a REVIEWED
#: decision about whose bytes this realm vouches for, never an unattended
#: rewrite.
#:
#: NOT REWRITTEN IS NOT THE SAME AS NOT LOOKED AT, and conflating the two cost
#: this realm four layers of drift. Excluding includes from `sections()` keeps
#: them out of the rewrite, which is right. It also made them invisible: this
#: manifest holds ONLY includes, so every scan examined zero entries and
#: reported "nothing moved" while the composition pinned pulseengine 2026.10.4
#: and that line reached 2026.10.8. The realm looked healthy because the
#: scanner had nothing to say and no way to say it had nothing to say.
#:
#: So includes are reachable through `includes()` below and reported by the
#: scanner as needing a person — the same channel the hub case already uses.
#: They never enter the rewrite list.
NOT_PAYLOADS = frozenset({"include"})


def sections(manifest: dict) -> list[str]:
    """Every payload section the manifest defines, in its own order."""
    return [
        k
        for k, v in manifest.items()
        if isinstance(v, list) and k not in NOT_PAYLOADS
    ]


def entries(manifest: dict):
    """Every payload entry, as (section, entry)."""
    return [(s, e) for s in sections(manifest) for e in manifest[s]]


def includes(manifest: dict) -> list[dict]:
    """Every composition edge the manifest declares, in its own order.

    Separate from `entries()` on purpose: an include is never rewritten, and
    keeping the two accessors apart means the rewrite cannot acquire one by
    accident. What an include IS, for a reader asking what moved: a realm name,
    a layer id on that realm's line, and the digest of that layer's signed
    manifest.
    """
    return list(manifest.get("include", []))


def repo_of(entry: dict) -> str:
    """The `owner/repo` an entry comes from; `pulseengine/<name>` by default."""
    return entry.get("repo", f"pulseengine/{entry['name']}")


def asked_tag(entry: dict) -> str:
    """The release tag this entry asks a forge for."""
    return entry.get("release", entry["version"])


def tracks_tag(entry: dict) -> bool:
    """Does this entry's `version` follow the release tag?

    True with no `release` (the version IS the tag), or when the two agree once
    a leading `v` is discounted. False for a hub payload, whose number is its
    own and cannot be derived from a tag.
    """
    release = entry.get("release")
    if release is None:
        return True
    version = entry["version"].lstrip("v")
    if version == release.lstrip("v"):
        return True
    # A tag that merely DECORATES the version still yields it: binaryen
    # releases `version_133` for version 133, so a scanner seeing
    # `version_134` can derive 134 and bump on its own. What it cannot derive
    # is a hub's number — `with-device` is 0.2.2 inside release v0.7.2, and no
    # amount of string handling gets from one to the other.
    #
    # Required: the tag ENDS WITH the version, and what precedes it contains no
    # digits. Without that second half, release `v1.2.10` would "derive"
    # version `10`.
    if release.endswith(version):
        prefix = release[: -len(version)]
        return not any(c.isdigit() for c in prefix)
    return False


def registry_ref(manifest: dict) -> str:
    """The registry this realm publishes to, as `host/repo` (no scheme).

    Read from the manifest, never defaulted in code. `next-layer-id.sh` used to
    default to `ghcr.io/pulseengine/layers`, which is correct in exactly one
    repository and silently wrong in every other — copied into a second realm it
    derived that realm's next layer id and COUNTER from another realm's
    published record. The counter is the per-line anti-rollback high-water mark,
    so a first layer would have claimed a history it does not have.
    """
    registry = manifest["realm"]["registry"]
    if not registry.startswith("oci://"):
        raise ValueError(
            f"realm.registry is {registry!r}; varve addresses a realm's registry as "
            f"oci://<host>/<repo>, and everything downstream strips that scheme"
        )
    ref = registry.removeprefix("oci://").rstrip("/")
    if "/" not in ref:
        raise ValueError(f"realm.registry {registry!r} names a host with no repository path")
    return ref
