from crawl4ai.content_filter_strategy_lxml import PruningContentFilterLXML

class MarkdownGenerationResult:
    raw_markdown: str
    fit_markdown: str

class DefaultMarkdownGenerator:
    def __init__(self, *, content_filter: PruningContentFilterLXML, options: dict[str, str | int | bool]) -> None: ...
    def generate_markdown(self, input_html: str, *, base_url: str, citations: bool) -> MarkdownGenerationResult: ...
