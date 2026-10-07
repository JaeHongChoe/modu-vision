# Remote terminal metrics and malformed-journal recovery

The native specialist receipt stores a32-character model ID; its launch journal
stores `job_` plus that ID. The old reader compared these unequal IDs and reset
completed epoch/loss displays. Recovery now checks the exact pair, original
launch alias, allowed family, output, snapshot/source, profile, terminal state and
training provenance before copying metrics. Direct launch journals also retain
their original identity specification. Original core IDs still read back; foreign
or ambiguous receipts contribute no metrics and remain unchanged.

Malformed JSON objects or launch specifications previously raised before the
recovery loop could reach valid ended records. Non-object journals are reported
and retained; invalid priority specifications cannot break the ordering pass.
No unsupported record is turned into a new worker or lease.

Related **229 tests passed with no skips**. A clean committed **45-case subset**
passed again; it overlaps the229 and is not an additional task count. The actual
CPU rotation case trains one epoch through the local worker and synchronous
loopback transfer, then preserves its exact epoch, train/validation loss, model
bytes and receipt on fresh readback. Network GPU and detached-worker liveness
were controlled boundaries; no human manufacturing quality is approved.

The retained failures include the reproduced3-to0 epoch display, malformed JSON
and launch exceptions, a deliberately corrupted output fixture correctly refused
by the saver, a temporary indentation error, and internal disk exhaustion. Final
verification uses new external temporary directories; failed logs remain intact.

## Test storage

Two ended owned temporary trees had accumulated7,800,927,020 bytes of repeated
pretrained payloads in FakeRemote control cases. They were copied externally;
7,267 entries and protected source/log hashes matched before their internal
copies were removed. Internal free space changed from148,606,976 to8,257,511,424
bytes. No original checkout, user data or handoff evidence was removed.

The FakeRemote reliability/operation controls now reuse the bounded transfer
fixture already used by coordinator controls. The final related test tree has
97 transferred `pretrained.safetensors` files totaling4,943 bytes, each at most51
bytes. This is protocol evidence; authentic-weight execution retains its own
qualification suites. The actual CPU rotation case remains exercised.

S1-08 and S6-05 keep their pending acceptance boundaries. Ended specialist status
readback does not qualify specialist relocation migration, live/uncertain worker
adoption or unrelated operation histories. Latest complete hosted CI,72-hour
endurance, target devices and external quality/release approval remain pending.

Source: `4d4ccd885057702d05a844b3ef3570fc585d6137`
Receipt: `docs/verification/receipts/2026-10-07-remote-terminal-identity.json`
Receipt SHA256: 7d37fcf47538495ba309ed7acc95da93895b3273e7ed20f854320fb3366ec845
