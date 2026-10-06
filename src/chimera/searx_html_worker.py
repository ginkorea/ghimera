"""Parse the ordinary SearXNG simple theme without a browser or secondary fetch."""

import contextlib
import sys
from urllib.parse import urljoin

from chimera.html_worker import no_network
from chimera.refusals import ChimeraRefused, RefusalCode
from chimera.research_types import SearchHit
from chimera.search_html_types import SearchHtmlRequest, SearchHtmlResponse


def parse(request: SearchHtmlRequest) -> tuple[SearchHit, ...]:
    from scrapling.parser import Selector

    # Selectors are the provider's simple-theme wire structure, not operator
    # endpoints or general-purpose publisher locators. Unknown layouts refuse.
    source = request.page.body.decode(request.encoding, errors="strict")
    root = Selector(source, url=request.page.final_url, huge_tree=False)
    containers = root.css("#results #urls")
    if len(containers) != 1:
        raise ChimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
    hits: list[SearchHit] = []
    for article in containers[0].css("article.result")[: request.limit]:
        links = article.css("h3 a[href]")
        if len(links) != 1:
            raise ChimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
        link = links[0]
        href: object = link.attrib.get("href")
        if not isinstance(href, str) or not href.strip():
            raise ChimeraRefused(RefusalCode.SEARCH_UNAVAILABLE)
        snippets = article.css("p.content")
        hits.append(
            SearchHit(
                url=urljoin(request.page.final_url, href),
                title=str(
                    link.get_all_text(separator=" ", strip=True, ignore_tags=("script", "style"))
                ),
                snippet=" ".join(
                    str(
                        node.get_all_text(
                            separator=" ", strip=True, ignore_tags=("script", "style")
                        )
                    )
                    for node in snippets
                ),
            )
        )
    return tuple(hits)


def _cmd_parse() -> int:
    sys.addaudithook(no_network)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            request = SearchHtmlRequest.model_validate_json(sys.stdin.buffer.read())
            hits = parse(request)
        response = SearchHtmlResponse(hits=hits)
    except ChimeraRefused as exc:
        response = SearchHtmlResponse(refusal=exc.code)
    except Exception:
        # Untrusted vendor/HTML boundary; never emit vendor diagnostic bodies.
        response = SearchHtmlResponse(refusal=RefusalCode.SEARCH_UNAVAILABLE)
    sys.stdout.write(response.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_parse())
