from dataclasses import asdict

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct

from app.conf.app_config import app_config
from app.entities.column_info import ColumnInfo


class ColumnQdrantRepository:
    collection_name = "column_info_collections"
    def __init__(self, client: AsyncQdrantClient):
        self.client = client

    async def ensure_collection(self):
        if not await self.client.collection_exists(self.collection_name):
            await self.client.create_collection(self.collection_name,
                                                vectors_config=VectorParams(size=app_config.qdrant.embedding_size,
                                                                            distance=Distance.COSINE))

    async def upsert(self, ids: list[str], embeddings: list[list[float]], payloads: list[ColumnInfo], batch_size: int = 20):
        zipped = list(zip(ids, embeddings, payloads))
        for i in range(0, len(zipped), batch_size):
            # 截取一批数据
            batch = zipped[i:i + batch_size]
            # 组装Qdrant点位结构
            batch_points = [
                PointStruct(id=id, vector=embedding, payload=asdict(payload))
                for id, embedding, payload in batch
            ]
            # 批量插入/更新向量库
            await self.client.upsert(
                collection_name=self.collection_name,
                points=batch_points
            )