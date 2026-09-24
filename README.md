# covalent-layers

The **`covalent`** realm: a layer that carries **nothing of its own** and says
one thing — *these two toolchains, together, are a toolchain.*

```toml
# varve-realms.toml
[realm.covalent]
registry   = "oci://ghcr.io/pulseengine/covalent-layers"
trust-root = "32d09d12ec3dcd448fcfd9f5768327126d3d9c54a74fe01266882b43362eb8a5"
```

```toml
# varve.toml — one pin, both halves
manifest-version = 1

[toolchain]
realm   = "covalent"
channel = "rolling"
layer   = "2026.09.0"
```

`varve install` then walks the composition and fetches each included layer
from **its own realm's registry**, verified against **its own realm's root**.

## What it composes

| realm | what it carries | whose root signs it |
|---|---|---|
| `pulseengine` | rivet, spar, synth, witness, ordeal, loom, meld, kilnd, wsc, … | PulseEngine |
| `pulseengine-wasm` | wasm-tools, wac, wkg, wit-bindgen, wasmtime, binaryen, wrpc | PulseEngine |
| **`covalent`** | **nothing — only the pairing** | **PulseEngine** |

## Why a realm that carries nothing

A project building WebAssembly components with the PulseEngine methodology
needs both halves. Before this it pinned two realms in two directories and
hoped they moved together. The pairing was a fact in somebody's head.

Now it is a signed artifact. This realm's root signs **only** the claim that
these two layers belong together; each included layer is still verified
against its own realm's root, by digest. A composing realm therefore **cannot
widen trust** — it can assert a pairing and nothing more. If either upstream
root rejects its layer, the composition fails.

That is also why an `[[include]]` names a realm **and a digest**: a tag could
be moved after this layer is signed, and an include with no realm would fall
back to the *pinning project's* root — installing cleanly and verifying
against the wrong key.

## What you are trusting

Read this before pinning. Three of the four payloads in `pulseengine-wasm`
are ingested with **no proof of origin** — those upstream releases publish
neither cosign-signed sums nor build provenance — and each travels with the
operator's recorded reason inside the signed layer. `varve inspect` shows them
as `proof = unverified` with the reason attached. Composing that realm means
accepting those reasons.

## Changing what this layer composes

Edit `layer.toml`. **That file is the layer**; bumping an included digest is
the whole change. Nothing here is vendored, and no workflow encodes the
contents.
