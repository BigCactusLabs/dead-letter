# Draft-first immutable release sequencing

Status: **tracked design, not implemented or enabled**. Follow-up to issue #125.
The current authority model and published-release event remain unchanged.

## Why enabling immutability now is unsafe

Today a maintainer publishes a stable GitHub release, which authorizes the PyPI
workflow. MCPB and OCI are built after PyPI readiness; MCPB and the resolved MCP
Registry manifest are then attached to the already-published GitHub release.
Immutable releases do not permit that post-publication asset attachment.

The [GitHub recommendation](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
is draft → attach all assets → publish. The present workflow cannot be converted
by changing a repository setting or moving its existing publish event earlier.
The [publishing runbook](../reference/publishing.md) remains authoritative until
a separate reviewed implementation changes this sequence.

## Proposed gated sequence

1. A maintainer chooses the stable version and exact reviewed main commit.
   An explicit preparation dispatch checks metadata, ancestry, and source tests,
   builds the wheel/sdist once, and records immutable build evidence.
2. The existing release environment authorizes PyPI upload of those tested bytes.
   This replaces the current public-release-event authority and therefore needs
   an explicit policy/workflow review, not an implicit helper side effect.
3. After both PyPI indexes are ready, build/smoke MCPB and OCI; retain every exact
   byte/digest. Create or reuse a draft GitHub release at the recorded tag/commit.
4. Attach the original wheel/sdist evidence, MCPB/sidecar, resolved registry
   manifest/sidecar, and a completion ledger to that draft. Verify the entire
   inventory before allowing publication. No checksum may be inferred from a
   different rebuild; retry only a failed downstream phase.
5. A maintainer publishes the fully populated draft. GitHub locks its tag/assets.
   Record and verify the resulting release attestation. The published event must
   **not** accidentally trigger the old PyPI upload chain a second time.
6. Publish the exact archived manifest to the MCP Registry, referencing public
   GitHub asset URLs and the tested OCI digest. A registry-only failure can retry
   the same manifest without modifying the immutable release. Plugin adoption
   and the Homebrew PR stay independent and manually authorized.

PyPI precedes public GitHub publication in this design because current bundle
smokes install the exact published dependency. That means a failed draft phase
can leave PyPI published: failure recovery must preserve that fact rather than
pretend the sequence is atomic. A future staging mechanism needs its own design.

## Acceptance before enabling the setting

Tests must prove exact-byte reuse; draft inventory completeness; prevention of
wrong-commit/tag publication; behavior for already-uploaded PyPI files; expired
build evidence; missing sidecars; concurrent preparation; invalid/immutable old
releases; registry-only retry; and prevention of double publication when the
draft becomes public. Review how the triggering identity interacts with GitHub
workflow recursion rules and environment approval. Do not introduce a broad
cross-repository token as a shortcut.

Run a maintainer-authorized rehearsal, record partial-failure recovery evidence,
and update AGENTS.md, Publishing, workflow contracts, and release-status guidance
in the same implementation. Only then consider enabling immutable releases.

The new [read-only status helper](../reference/release-operations.md) can flag an
immutable release with missing assets; that is not implementation or validation
of this proposed sequence.
