import asyncio
from contextlib import asynccontextmanager

from pricewatch.bootstrap import apply_runtime_schema


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.commits = 0

    async def execute(self, query: str, params=None):
        self.calls.append(query)
        return None

    async def commit(self) -> None:
        self.commits += 1


class FakeFactory:
    def __init__(self) -> None:
        self.connections: list[FakeConnection] = []

    @asynccontextmanager
    async def __call__(self):
        connection = FakeConnection()
        self.connections.append(connection)
        yield connection


def test_runtime_schema_bootstrap_applies_base_then_quality_migration() -> None:
    factory = FakeFactory()

    asyncio.run(apply_runtime_schema(factory))

    assert len(factory.connections) == 2
    base_sql = " ".join(factory.connections[0].calls).lower()
    quality_sql = " ".join(factory.connections[1].calls).lower()
    assert "create table if not exists tracked_product" in base_sql
    assert "offer_quality_observation" not in base_sql
    assert "create table if not exists offer_quality_observation" in quality_sql
    assert "quality_status" in quality_sql
    assert [connection.commits for connection in factory.connections] == [1, 1]
