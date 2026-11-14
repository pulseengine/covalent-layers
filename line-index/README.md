# The realm's line index — committed here, never read from the registry

One document per line, `<line>.json`, naming every layer this realm has
published on it:

```json
{
  "line": "2026.10",
  "counter": 3,
  "issued-at": "2026-10-01T17:44:00Z",
  "layers": [
    { "layer": "2026.10.4", "digest": "sha256:…", "channel": "rolling", "counter": 24 }
  ]
}
```

Each deposit appends the layer it just pushed (`tools/append-index.py`,
`.github/workflows/deposit.yml`), signs the document with the realm root, and
publishes the envelope under the `line-index-<line>` tag. `varve` reads it from
there and refuses an enumeration that omits a layer the index names.

## Why the list is here and not derived from `oras repo tags`

Because the index exists to catch a registry **hiding** a layer. Every artifact
a hiding registry does serve still verifies, so without an independent list
there is nothing to notice.

Derive the list from the registry and the check becomes vacuous: a hiding
registry omits the layer from the tag listing *and therefore from the index*,
the two agree perfectly, and varve reports nothing wrong. It would pass its own
tests, publish real signatures, and detect nothing — the most expensive kind of
wrong, because everyone downstream believes it. This is varve DD-027.

The accepted cost is a reviewed file and a possible merge conflict when two
deposits race; the per-layer concurrency group makes that rare, and the deposit
rebases before pushing.

## The layers published BEFORE this existed are not in here

This realm had published 23 layers by the time the publisher shipped, and none
of them are named here. That is deliberate, not an omission to tidy up later.

The only place their payload digests could be read from is the registry — and a
registry-derived entry gives **no protection for the layer it names**, by
exactly the argument above: a registry that had hidden one of those layers
would have hidden it from the backfill too. Writing them in would produce a
document that looks complete and protects nothing, which is worse than a short
document that is honest about its reach.

So the index is a **floor**, which is what varve treats it as — extra layers a
source serves are never an error. Omission detection covers every layer
deposited from this point on.

## Do not set `signed-index = true` on the realm until this has published once

A realm declaring it with no index published **fails closed on every install**.
The declaration is the last step, and it lives in varve's `varve-realms.toml`,
not here.

## What this realm's index does NOT name, and why

This realm published `2026.09.0` (2026-09-24) and `2026.10.0` (2026-10-08)
**before** this publisher existed. Neither is in an index document, and neither
will be backfilled.

A backfill would have to read their digests from the registry, and a
registry-derived entry protects nothing: a registry that had hidden one of them
would have hidden it from the backfill too, so the index and the listing would
agree and varve would report nothing wrong. That is varve's DD-027 argument
applied to this realm's own history.

So the index is a **floor**: from the next deposit onward, a layer this realm
publishes is named in it, and a source that omits a named layer is refused.
Extra layers a source serves are never an error, which is what makes a partial
index safe to publish rather than a reason to wait.

### The consequence for `covalent-layers`

`covalent-layers` composes this realm by digest, and its scanner can only
propose a move when the included realm publishes a signed index for the line
the pin sits on. Its pin is `2026.09.0`, on line `2026.09`, which has no index
and will not get one — so that edge stays unanswerable until the pin is moved
to a `2026.10` layer **once, by hand**. After that it is answerable, because
line `2026.10` gets an index at the next deposit here.

One manual move, stated rather than discovered: the alternative is a backfill
whose entries prove nothing.
