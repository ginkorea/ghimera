# Canonical content, retained occurrences and drift

Status: standalone candidate, not deployed. `chimera.dedup/1` supplies the
tracking-parameter names/prefixes, character-shingle width, Hamming threshold,
minimum near-match length, length-ratio bar and index/text limits. The safe
main example enables the policy. Absence preserves the earlier byte-only
reader/loop behavior for historical candidates; production construction must
choose the explicit policy, not an ambient default.

`ContentIndex` belongs to one collection session and persists across research
rounds. The original final URL stays unchanged. Canonicalization removes only
configured tracking keys and fragments, normalizes host/default port, and keeps
meaningful query pairs. A same-host `rel=canonical` is a grouping hint; it is
never permission to fetch elsewhere or proof that changed content is identical.

Matching uses raw SHA-256 first, language-bound normalized-text SHA-256 second,
and deterministic 64-bit Unicode character-shingle SimHash third. Normalization
is NFKC, casefold and whitespace collapse; originals are never rewritten.
Disjoint fingerprint bands retrieve every candidate within the configured
Hamming radius, followed by actual Hamming/length checks. Near matches do not
cross declared languages, unknown-language near matching is refused, short
text is exact-only, and a changed numeric sequence remains separate evidence.
These are similarity decisions, not calibrated probabilities or equivalence
of factual meaning; corpus-specific false-merge evaluation remains necessary.

The first accepted source represents its cluster. `duplicate_urls` projects
retained `DuplicateOccurrence` records, each with its own raw bytes, digest,
native extraction/layout, verdict, transport and policy-bound dedup evidence.
The shared `DocumentSource` base enforces source binding for both. Each source
still pays for its own judge verdict; this change does not reuse a publisher
assessment merely because two pages are similar. Saturation/accepted counts
use representatives, while research/model contexts can cite every occurrence.
Citation lookup includes raw digest **and source URL**, so byte-identical
mirrors cannot shadow one another. The reader recomputes duplicate evidence
and validates occurrence configuration, source bytes and extraction.

The mandatory duplicate ledger carries explicit reason, source fingerprint,
representative digest, Hamming distance and policy digest. Within a session,
changed bytes observed under the same canonical URL produce a content-drift
row distinguishing changed native text from changed source bytes. Both retained
revisions are required on read. This is content-change evidence, not the
separate locator-miss/drift doctor required by F2; cross-run persistence and
archived publisher redesign acceptance remain open.

The index refuses its configured capacity/text limit rather than silently
evicting evidence or truncating a document before comparing it. No model,
network, new database or global mutable registry is used by identity matching.

Remaining C2 work: representative false-merge evaluation across mission
languages, locator drift/doctor and archived before/after redesign corpus,
model-based PDF layout/OCR, Marker and their offline artifact recipes.
