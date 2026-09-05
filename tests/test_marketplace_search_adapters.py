import asyncio
import json
from pathlib import Path

from pricewatch.adapters.ozon import OzonSearchAdapter
from pricewatch.adapters.wildberries import WildberriesSearchAdapter
from pricewatch.marketplaces import SearchRequest

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingFetcher:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.requests: list[SearchRequest] = []

    async def get_json(self, request: SearchRequest) -> dict:
        self.requests.append(request)
        return self.payload


def test_wb_adapter_builds_current_v9_search_request() -> None:
    fetcher = RecordingFetcher(fixture("wb_search_minimal.json"))
    adapter = WildberriesSearchAdapter(fetcher, dest="-1257786")

    candidates = asyncio.run(adapter.search("xiaomi pad 7 8 256", limit=50, page=2))

    assert len(candidates) == 1
    request = fetcher.requests[0]
    assert request.url == "https://search.wb.ru/exactmatch/ru/common/v9/search"
    assert request.params["query"] == "xiaomi pad 7 8 256"
    assert request.params["page"] == "2"
    assert request.params["dest"] == "-1257786"
    assert request.params["resultset"] == "catalog"
    assert request.params["curr"] == "rub"


def test_ozon_adapter_builds_composer_search_request() -> None:
    fetcher = RecordingFetcher(fixture("ozon_search_minimal.json"))
    adapter = OzonSearchAdapter(fetcher)

    candidates = asyncio.run(adapter.search("xiaomi pad 7 8 256", limit=50, page=3))

    assert len(candidates) == 1
    request = fetcher.requests[0]
    assert request.url == "https://www.ozon.ru/api/composer-api.bx/page/json/v2"
    assert request.params == {"url": "/search/?text=xiaomi pad 7 8 256&page=3"}


def test_ozon_adapter_uses_category_scope_when_supplied() -> None:
    fetcher = RecordingFetcher(fixture("ozon_search_minimal.json"))
    adapter = OzonSearchAdapter(fetcher)

    asyncio.run(
        adapter.search(
            "xiaomi pad 7 8 256",
            category_path="/category/planshety-15525/",
            page=2,
        )
    )

    request = fetcher.requests[0]
    assert request.params == {
        "url": "/category/planshety-15525/?text=xiaomi pad 7 8 256&page=2"
    }


def test_ozon_adapter_rejects_arbitrary_category_scope() -> None:
    adapter = OzonSearchAdapter(RecordingFetcher(fixture("ozon_search_minimal.json")))

    try:
        asyncio.run(adapter.search("x", category_path="https://evil.example/x"))
    except ValueError as exc:
        assert "category" in str(exc)
    else:
        raise AssertionError("arbitrary category path must fail")


def test_search_adapters_enforce_positive_page_and_limit() -> None:
    wb = WildberriesSearchAdapter(RecordingFetcher({"products": []}))
    ozon = OzonSearchAdapter(RecordingFetcher({"widgetStates": {}}))

    for adapter in (wb, ozon):
        try:
            asyncio.run(adapter.search("x", page=0))
        except ValueError as exc:
            assert "page" in str(exc)
        else:
            raise AssertionError("page=0 must fail")

        try:
            asyncio.run(adapter.search("x", limit=0))
        except ValueError as exc:
            assert "limit" in str(exc)
        else:
            raise AssertionError("limit=0 must fail")
