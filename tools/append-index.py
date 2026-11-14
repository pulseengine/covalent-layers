#!/usr/bin/env python3
"""Append a deposited layer to this realm's committed line-index document.

THE LIST IS COMMITTED HERE, NOT READ FROM THE REGISTRY, and that is the whole
point (varve DD-027). The index exists so a consumer can tell that a registry
is HIDING a layer: every artifact a hiding registry does serve still verifies,
so without an independent list there is nothing to notice. Derive the list from
`oras repo tags` and the check becomes vacuous — a hiding registry omits the
layer from the listing and therefore from the index, the two agree perfectly,
and varve reports nothing wrong. It would pass its own tests, publish real
signatures, and detect nothing.

So each deposit appends the layer it just pushed, here, in a file a person
reviews.

IDEMPOTENT, because a deposit is re-runnable. Appending the same layer twice
must not duplicate the entry and must not bump the document counter — a
re-dispatch after a transient registry failure is ordinary, and a counter that
climbed on a no-op would make the next legitimate publish look stale.

A layer already present with a DIFFERENT digest is a refusal, not an update.
Two different sets of bytes under one layer id is the thing varve's immutability
rule exists to prevent, and quietly rewriting the entry would hide it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys


def layer_key(layer: str) -> tuple[int, ...]:
    """Order as varve's own LayerId does: (year, month, patch), NUMERICALLY.

    A string sort puts `2026.09.12` before `2026.09.2`, which is wrong by eight
    layers on a line that has run that long.
    """
    return tuple(int(p) for p in layer.split("."))


def append(doc: dict, layer: str, digest: str, channel: str, counter: int,
           issued_at: str) -> tuple[dict, bool]:
    """Return (document, changed). `changed` is False for an exact re-append."""
    if not digest.startswith("sha256:"):
        raise SystemExit(f"error: digest {digest!r} is not a sha256 reference")
    layers = list(doc.get("layers", []))
    for existing in layers:
        if existing["layer"] != layer:
            continue
        if existing["digest"] == digest:
            return doc, False
        raise SystemExit(
            f"error: {layer} is already in the index at {existing['digest']} and "
            f"this deposit carries {digest}. Two different sets of bytes under one "
            f"layer id is what varve's immutability rule refuses; the index will "
            f"not be rewritten to agree with a republish."
        )
    layers.append(
        {"layer": layer, "digest": digest, "channel": channel, "counter": counter}
    )
    layers.sort(key=lambda e: layer_key(e["layer"]))
    return (
        {
            "line": doc["line"],
            # The DOCUMENT counter, not the layer's: a consumer refuses an index
            # older than the one it holds, so this climbs once per real change.
            "counter": doc.get("counter", 0) + 1,
            "issued-at": issued_at,
            "layers": layers,
        },
        True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, type=pathlib.Path)
    ap.add_argument("--layer", required=True)
    ap.add_argument("--digest", required=True, help="the layer's SIGNED PAYLOAD digest")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--counter", required=True, type=int, help="the LAYER's counter")
    ap.add_argument("--issued-at", required=True)
    args = ap.parse_args()

    line = args.layer.rsplit(".", 1)[0]
    if args.file.exists():
        doc = json.loads(args.file.read_text())
        if doc["line"] != line:
            raise SystemExit(
                f"error: {args.file} is the index for line {doc['line']}, and "
                f"{args.layer} belongs to {line}"
            )
    else:
        # A new line starts its own document at counter 0; the append below
        # takes it to 1.
        doc = {"line": line, "counter": 0, "issued-at": args.issued_at, "layers": []}

    doc, changed = append(
        doc, args.layer, args.digest, args.channel, args.counter, args.issued_at
    )
    if not changed:
        print(f"{args.layer} is already in {args.file} at {args.digest} — unchanged")
        return 0
    args.file.parent.mkdir(parents=True, exist_ok=True)
    args.file.write_text(json.dumps(doc, indent=2) + "\n")
    print(
        f"{args.file}: index #{doc['counter']} now names "
        f"{len(doc['layers'])} layer(s), newest {doc['layers'][-1]['layer']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
