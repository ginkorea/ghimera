"""One owned passive feed parser process; source/network access is unavailable."""

import sys

from ghimera.html_worker import no_network
from ghimera.source_feed_parse import parse_feed
from ghimera.source_feeds import SourceFeedRequest


def main() -> int:
    sys.addaudithook(no_network)
    try:
        request = SourceFeedRequest.model_validate_json(sys.stdin.buffer.read())
        result = parse_feed(
            request.page.body,
            request.page.final_url,
            request.page.content_type.split(";", 1)[0].strip().lower(),
            request.policy,
        )
        sys.stdout.buffer.write(result.model_dump_json().encode())
        return 0
    except ValueError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
