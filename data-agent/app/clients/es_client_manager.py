from qdrant_client import AsyncQdrantClient

from app.conf.app_config import ESConfig, app_config
import asyncio
import os
from elasticsearch import AsyncElasticsearch

class EsClientManager:
    def __init__(self, config: ESConfig):
        self.client : AsyncElasticsearch | None = None
        self.config : ESConfig = config

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        self.client = AsyncElasticsearch(
        hosts=[self._get_url()],
    )

    async def close(self):
        await self.client.close()

es_client_manager = EsClientManager(app_config.es)

if __name__ == "__main__":
    es_client_manager.init()
    client = es_client_manager.client

    async def test():
        # 创建索引
        # await client.indices.create(
        #     index="books",
        # )

        # 写入数据
        await client.index(
            index="books",
            document={
                "name": "Snow Crash",
                "author": "Neal Stephenson",
                "release_date": "1992-06-01",
                "page_count": 470
            },
        )

        # 查询数据
        resp = await client.search(
            index="books",
        )
        print(resp)
        await client.close()

    asyncio.run(test())