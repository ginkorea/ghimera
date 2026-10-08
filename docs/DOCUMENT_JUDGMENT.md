# Source-bound scored document judgment

`document_judgment` is optional, versioned and explicitly selected. Its absence
preserves the legacy configured-model prompt, serialization and verdict behavior.
The legacy judge starts at the first matching goal term, not necessarily the
document prefix. It does not consume the scorer's best windows.

The opt-in [example](../examples/scored_document_judgment.toml) requires native
intent scoring, bound models, the existing private journal and retained native
model results. Window/count/character limits must fit the original judge and
scoring recipe. It adds no encoder calls, model fallback, quota extension or
whole-source retry. Existing model input/request/result limits still apply.

Before encoding, the native scorer commits one exact full native text reading
to the **existing journal** with its original URL, raw-source SHA, text SHA and
prior parser-row sequence/digest. Journal capacity/fsync admission occurs before
contact. The scoring row references this one immutable observation by sequence
and exact row digest; identical text from another raw source is not a binding.
These are private operational replay records, not accepted corpus documents.
This path retains no images or decorations. Generated PDF readings are not
relabeled native evidence.

One reusable selector ranks observed native windows by decreasing cosine and
then original offsets. It expands only unchanged source text within the explicit
padding/per-window/total limits, merges overlapping or adjacent spans when their
union fits, and never double-counts overlapping characters. Selected spans retain
their exact scoring anchors, reference bindings, source hashes and omitted count.
Similarity is not a truth, relevance or acceptance probability. Original intent
references are validated before model contact. Legacy score rows without raw
source provenance cannot supply the new policy.

The journal records the chosen context and exact logical input digest before
the native verdict intent. Only selected excerpts go to the model; full private
text is not put in its prompt. Model-call evidence records the actual offsets,
context digest and omissions under the explicitly selected prompt revision.

`ghimera.document-judgment/1` retains its original instructions, serialized policy
bytes and `ghimera-scored-document-judgment/1` revision. It must not contain
`prompt_profile`, including an explicit null. There is no implicit upgrade.
`ghimera.document-judgment/2` requires the explicit
`prompt_profile = "contribution_relevance"` shown in the inert example, and records
`ghimera-scored-document-judgment/2`. All source selection, provenance, limits,
reservations and output schemas remain unchanged.

The contribution profile asks the model to assess every supplied window for
direct factual support of any part of the intent. Source admission is not answer
completeness: other unanswered facts, including unresolved names or dates, remain
downstream gaps. Quotes and reasons must be grounded in supplied excerpts, not
topic names, titles or similarity scores. Demonstrably unrelated excerpts may
be rejected; ambiguous or conflicting evidence calls for hold. Unread text cannot
justify a whole-source negative claim. The profile does not make a client-side
relevance decision or force acceptance; later assessment/review still determine
answer sufficiency and citation support.

The original model result and ACK are unchanged. Separate verdict evidence names
`original_model_decision` and `client_disposition`. Only this explicit policy
converts a reject with unread native characters to a client-side hold. This is
not fabricated model agreement or human approval. The existing second-look
allowance may consume another separately charged model reservation; an expanded
partial reject still holds. Quota or input/storage refusal does not become a
whole-document negative claim. UNKNOWN retains existing charged uncertainty and
is never silently retried by selection.

Both unsealed journal readback and Harvest validation check the prior parser/raw
binding, retained full text, exact original scoring row, deterministic padding,
reference/goal bindings, reservation/intent/ACK chain and original returned
verdict. There is no adoption of arbitrary historical source/model/graph state.

The retained regression fixture derives from the whole public constitution PDF
at <https://download.12371.cn/wenjian/2022/11/1/djcbesddz.pdf> (520,791 bytes,
raw SHA `0b75acd4280e0b331ac479996411b0dd652350f8fce903d117fcb942028167cc`).
It retains the complete 24,400-character native reading, vendor layout and actual
scoring observations, not a fabricated source subset. Article 23 begins at
offset 15936, outside the historical first-match excerpt `[13219,14719)`; the
highest observed score windows were `[15660,15960)` and `[15930,16230)`.
Tests use no-contact encoders and explicit model-protocol fixture responses.
They establish provenance/selection/accounting behavior, not real trained-model
quality, whole-Collector acceptance or semantic-window policy quality.
