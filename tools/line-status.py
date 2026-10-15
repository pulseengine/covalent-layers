#!/usr/bin/env python3
"""A line's line-status, kept in this repository and made true in the registry.

THE DOCUMENT IS COMMITTED HERE (`line-status/<line>.json`), the same way the
line index is (varve DD-027). A merge is the approval; this tool turns the
approved document into the one the registry serves, and can do it again at
any time — a lost or never-published document is RE-CREATED from the file.

THE COUNTER IS MECHANICAL, NOT REVIEWED. Every deposit raises the line's
counter (its baseline follows the deposit counter), so a number written into
a pull request is stale by the time it merges. What a person approves is what
the document SAYS; the counter it is published under is the smallest that
supersedes both the published document and every deposited baseline.
Comparison therefore ignores `counter` and `issued-at`.

A DEPOSIT CARRIES THE PUBLISHED DOCUMENT FORWARD. A baseline used to say only
line/counter/issued-at/support-until, with a counter that outranked the
published correction — so every consumer of the next layer lost every known
problem and yank the line had. `carry_forward` builds the baseline from the
published document instead.

Subcommands print JSON and exit 0; a refusal exits 1 with a reason.
"""

from __future__ import annotations

import argparse
import json
import sys

MECHANICAL = ("counter", "issued-at")


class Refused(Exception):
    pass


def _said(d: dict | None) -> dict:
    return {k: v for k, v in (d or {}).items() if k not in MECHANICAL}


def _check_line(doc: dict, other: dict | None, what: str) -> None:
    if other is not None and other.get("line") != doc.get("line"):
        raise Refused(
            f"{what} covers line {other.get('line')!r} but this is {doc.get('line')!r}"
        )


def plan_publish(repo: dict, published: dict | None, *, newest_baseline: int,
                 issued_at: str) -> tuple[str, dict | None]:
    """What publishing `repo` requires, given what the registry serves now.

    - nothing published, or published says less → ("publish", document)
    - published says exactly what the repo says → ("up-to-date", None)
    - published says something the repo does not → ("registry-has-more", None):
      someone published out of band, and overwriting would drop it.
    """
    _check_line(repo, published, "the published document")
    if published is not None:
        if _said(published) == _said(repo):
            return "up-to-date", None
        if _says_more(published, repo):
            return "registry-has-more", None
    counter = max(int(repo["counter"]),
                  int(published["counter"]) + 1 if published else 0,
                  int(newest_baseline) + 1)
    out = dict(repo, counter=counter)
    out["issued-at"] = issued_at
    return "publish", out


def _says_more(published: dict, repo: dict) -> bool:
    """Does the published document state anything the repo's does not?"""
    repo_ids = {p["id"] for p in repo.get("known-problems", [])}
    if any(p["id"] not in repo_ids for p in published.get("known-problems", [])):
        return True
    repo_yanked = repo.get("yanked", {})
    return any(layer not in repo_yanked for layer in published.get("yanked", {}))


def carry_forward(published: dict | None, *, line: str, counter: int,
                  issued_at: str, support_until: str) -> dict:
    """The baseline a deposit attaches: its own window, the line's problems."""
    out = {"line": line, "counter": int(counter), "issued-at": issued_at,
           "support-until": support_until}
    if published is None:
        return out
    if published.get("line") != line:
        raise Refused(f"published document covers line {published.get('line')!r}, not {line!r}")
    for key in ("known-problems", "yanked", "min-counter"):
        if key in published:
            out[key] = published[key]
    out["counter"] = max(int(counter), int(published["counter"]) + 1)
    return out


def _load(path: str | None) -> dict | None:
    if not path:
        return None
    with open(path) as f:
        text = f.read().strip()
    return json.loads(text) if text else None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan-publish")
    p.add_argument("--file", required=True)
    p.add_argument("--published", default="")
    p.add_argument("--newest-baseline", type=int, required=True)
    p.add_argument("--issued-at", required=True)
    c = sub.add_parser("carry-forward")
    c.add_argument("--published", default="")
    c.add_argument("--line", required=True)
    c.add_argument("--counter", type=int, required=True)
    c.add_argument("--issued-at", required=True)
    c.add_argument("--support-until", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan-publish":
            act, out = plan_publish(_load(a.file), _load(a.published),
                                    newest_baseline=a.newest_baseline,
                                    issued_at=a.issued_at)
            print(json.dumps({"action": act, "document": out}))
        else:
            print(json.dumps(carry_forward(_load(a.published), line=a.line,
                                           counter=a.counter, issued_at=a.issued_at,
                                           support_until=a.support_until)))
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
