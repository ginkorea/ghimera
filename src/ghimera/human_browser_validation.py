"""Replay browser provenance against observed capture work, without live I/O."""

from collections import Counter
from typing import TYPE_CHECKING

from ghimera.human_browser_types import (
    BrowserDownloadEvidence,
    BrowserResponseEvidence,
    BrowserSourceEvidence,
    HumanBrowserEvidence,
    validate_assistance_navigation,
)

if TYPE_CHECKING:
    from ghimera.models import Harvest


def validate_harvest(harvest: "Harvest") -> None:
    policy = harvest.receipt.effective_config.human_browser
    captures: dict[str, BrowserSourceEvidence] = {}
    observed_ids: set[str] = set()
    content: set[tuple[str, str]] = set()
    actions: Counter[str] = Counter()
    inline_actions: Counter[str] = Counter()
    inline_reads: Counter[str] = Counter()
    navigation_hops: Counter[str] = Counter()
    for row in harvest.ledger:
        if row.browser_action is not None:
            action = row.browser_action
            if (
                policy is None
                or policy.navigation is None
                or action.policy_digest != policy.content_digest()
                or action.target_id != policy.target_id
                or not policy.permits(action.url)
            ):
                raise ValueError("source-action spend requires its configured browser and scope")
            if action.action == "main_frame_navigation":
                actions[action.url] += 1
            else:
                inline_actions[action.url] += 1
        evidence = row.human_browser
        if evidence is not None:
            evidence.validate_policy(policy)
            if evidence.navigation is not None:
                navigation_hops.update(hop.url for hop in evidence.navigation.hops)
            if isinstance(evidence, HumanBrowserEvidence) and evidence.pagination is not None:
                navigation_hops.update(
                    hop.url for hop in evidence.pagination.landing_navigation.hops
                )
            if evidence.tor is not None:
                navigation_hops.update(hop.url for hop in evidence.tor.navigation.hops)
            if not isinstance(evidence, BrowserResponseEvidence):
                for item in evidence.assistance:
                    if item.request.navigation is not None:
                        navigation_hops.update(hop.url for hop in item.request.navigation.hops)
            if isinstance(evidence, BrowserDownloadEvidence) and evidence.landing_navigation:
                navigation_hops.update(hop.url for hop in evidence.landing_navigation.hops)
            if isinstance(evidence, BrowserResponseEvidence) and evidence.navigation is not None:
                inline_reads[evidence.final_url] += 1
                if inline_reads - inline_actions:
                    raise ValueError(
                        "guarded inline bytes require their explicit second fetch spend"
                    )
            if evidence.capture_id in observed_ids:
                raise ValueError("a browser capture cannot be charged/replayed twice")
            observed_ids.add(evidence.capture_id)
            captures[evidence.capture_id] = evidence
            content.add((evidence.final_url, evidence.captured_sha256))
        if row.human_assistance:
            if policy is None or len(row.human_assistance) > policy.max_assistance_attempts:
                raise ValueError("failed assistance requires its configured attempt bound")
            capture_ids = {item.request.capture_id for item in row.human_assistance}
            if len(capture_ids) != 1:
                raise ValueError("failed assistance cannot mix separate captures")
            capture_id = next(iter(capture_ids))
            if capture_id in observed_ids:
                raise ValueError("failed capture cannot reuse another capture observation")
            observed_ids.add(capture_id)
            if (
                sum(item.request.observed_dom_bytes for item in row.human_assistance)
                > row.bytes_read
            ):
                raise ValueError("failed assistance cannot hide observed DOM spend")
            for attempt, item in enumerate(row.human_assistance, start=1):
                request = item.request
                validate_assistance_navigation(request, policy)
                if request.navigation is not None:
                    navigation_hops.update(hop.url for hop in request.navigation.hops)
                if (
                    request.attempt != attempt
                    or request.policy_digest != policy.content_digest()
                    or request.session_id != policy.session_id
                    or request.target_id != policy.target_id
                    or request.request_url != row.url
                    or not policy.permits(request.request_url)
                    or not policy.permits(request.final_url)
                    or request.reason not in policy.assistance_reasons
                    or request.observed_dom_bytes > policy.max_dom_bytes
                    or (attempt > 1 and row.human_assistance[attempt - 2].action != "resume")
                ):
                    raise ValueError(
                        "failed assistance must retain its exact source-policy binding"
                    )
        if navigation_hops - actions:
            raise ValueError(
                "native navigation hops cannot discard their prior source-action spend"
            )
    for document in harvest.source_documents:
        evidence = document.human_browser
        if evidence is None:
            if (document.url, document.sha256) in content:
                raise ValueError("browser-observed document cannot discard capture provenance")
        elif captures.get(evidence.capture_id) != evidence:
            raise ValueError("browser document requires its successful capture observation")
    if harvest.graph is not None:
        for node in harvest.graph.nodes:
            if node.retained_source is not None:
                # Harvest's retained-origin validator already binds the complete
                # original. Its old capture is not a current browser action.
                continue
            evidence = node.human_browser
            if evidence is None:
                if (node.source_url, node.content_sha256) in content:
                    raise ValueError("browser document node cannot discard capture provenance")
            elif captures.get(evidence.capture_id) != evidence:
                raise ValueError("browser graph document requires its capture observation")
