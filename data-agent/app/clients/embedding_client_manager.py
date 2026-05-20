import asyncio
import aiohttp
from typing import Optional, List

from app.conf.app_config import EmbeddingConfig, app_config


class LocalOpenAIEmbedding:
    def __init__(self, base_url: str):
        self.url = f"{base_url}/v1/embeddings"

    async def aembed_query(self, text: str) -> List[float]:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.url,
                json={
                    "input": text,
                    "model": "bge"
                }
            ) as resp:
                data = await resp.json()
                return data["data"][0]["embedding"]


class EmbeddingClientManager:
    def __init__(self, config: EmbeddingConfig):
        self.client: Optional[LocalOpenAIEmbedding] = None
        self.config = config

    def _get_url(self):
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        self.client = LocalOpenAIEmbedding(self._get_url())


embedding_client_manager = EmbeddingClientManager(app_config.embedding)

if __name__ == "__main__":
    embedding_client_manager.init()
    client = embedding_client_manager.client

    async def test():
        text = "How are you?"
        res = await client.aembed_query(text)
        print(res[:3])

    asyncio.run(test())