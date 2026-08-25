# Gate B reduction — Step 0 composition root

Checkpoint: `84e893531c` and the Step 0 worktree changes.

This step defines the supervised target without deleting or rearming the old
system. The target is the foreground CLI:

```text
python -m job_intel.product_search.gate_b_evidence_runner_v1 run-supervised \
  --corpus <corpus-rows.json> \
  --manifest <evidence-manifest.json> \
  --manifest-sha256 <external-manifest-sha256> \
  --output <state-directory> \
  --provider-factory gate_b_cli_smoke_fixture:provider_factory \
  --decision-request-factory gate_b_cli_smoke_fixture:decision_request_factory
```

The production seam is the provider and decision-request factory interface;
the smoke fixture uses the same interface and does not add a test-only branch.
The target is explicitly foreground and has no systemd dependency. The manifest
contract remains the forty-eight-row contract; the harness prepares forty-eight
pinned benign rows.

Until Step 1 builds the supervised collection spine, this entry point refuses
to run with the named invariant `supervised_collection_spine_v1`. The refusal
is in the entry point, is non-zero and names the unsatisfied invariant. It is
not controlled by configuration. Step 1 removes the guard in the same change
that completes the spine.

## Coexistence rule

The old systemd wrapper remains deliberately inert during Steps 0–3. Its
missing manifest/config inputs are an operational precondition while the old
and target systems coexist, not a validity claim and not the round-3 defect
being fixed. The Step 0 harness asserts that the old wrapper exits `2` during
input validation before any provider can be constructed. Do not repair its
inputs during this coexistence window: doing so would re-arm a second dispatch
path against the same corpus while the supervised spine is incomplete.

The harness also asserts the target's named-invariant refusal independently.
A successful Step 0 run therefore means both paths are observed in their
intended pre-reduction states; it does not claim that collection has executed.
