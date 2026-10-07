# Configurable slower, jittered browsing

Ghimera already uses Patchright, a Playwright-compatible Chromium driver, for
isolated rendering and explicitly configured same-browser human assistance.
No second browser driver is introduced for request pacing.

Set `per_host_delay_seconds`, `per_host_concurrency`,
`global_requests_per_second` and `global_concurrency` in the existing recipe.
Append the optional [cadence example](../examples/cadence.toml) to add bounded
uniform jitter above the minimum per-origin interval. Robots crawl-delay and
request-rate directives remain lower bounds. Jitter never makes requests faster
than those floors, nor guarantees that a publisher will not throttle them.

The versioned `ghimera.cadence/1` policy selects throttle status codes,
exponential backoff, its cap and whether to respect `Retry-After` delta seconds
or HTTP dates. A longer server-declared wait is not shortened to the local
backoff cap. Waiting consumes the existing deadline; too little remaining time
means a refusal before another request, not an early retry. Retry count is still
the run's existing `retry_budget`. An exhausted throttle cannot trigger another
source route to evade the origin's limit. Unknown or malformed headers use the
configured backoff, not repeated immediate requests.

`Politeness` owns origin state and global admission. Response cooldown is set
before releasing the request slot, applies to other paths on the same origin,
and never changes other origins' cadence. A cooled origin waits **without**
reserving global network capacity, so unrelated eligible hosts can proceed.
Effective non-secret cadence policy is retained in the existing run receipt;
actual fetch statuses, attempts and failures remain in the ledger. An absent
cadence section preserves legacy recipe serialization and throttle retry policy.

This controls collector-owned fetch admission, browser resources that use the
parent HTTP boundary, and selected top-level human-browser captures. It does
not claim control of all subrequests inside an operator-managed browser or
separate challenge gateway; their deployment must own their network limits.
Cooldown state is run-instance state, not persisted host reputation across
service restarts or separate workers. A shared persistent scheduler is a later
infrastructure requirement, not implicitly claimed by this change.
