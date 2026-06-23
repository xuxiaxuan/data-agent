import uuid
from pathlib import Path
from app.core.log import logger

from langchain_huggingface import HuggingFaceEndpointEmbeddings
from omegaconf import OmegaConf

from app.clients import embedding_client_manager
from app.conf.meta_config import MetaConfig
from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.table_info import TableInfo
from app.entities.value_info import ValueInfo
from app.models.column_info_mysql import ColumnInfoMysql
from app.models.table_info_mysql import TableInfoMySQL
from app.repositories.es.value_es_repository import ValueEsRepository
from app.repositories.mysql.dw import dw_mysql_repository
from app.repositories.mysql.dw.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta import meta_mysql_repository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMysqlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository


class MetaKnowledgeService:
    def __init__(self, meta_mysql_repository: MetaMysqlRepository,
                 dw_mysql_repository: DwMysqlRepository,
                 column_qdrant_repository: ColumnQdrantRepository,
                 embedding_client: HuggingFaceEndpointEmbeddings,
                 value_es_repository: ValueEsRepository,
                 metric_qdrant_repository: MetricQdrantRepository):
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository
        self.column_qdrant_repository = column_qdrant_repository
        self.embedding_client = embedding_client
        self.value_es_repository = value_es_repository
        self.metric_qdrant_repository = metric_qdrant_repository

    async def _save_tables_to_meta_db(self,meta_config: MetaConfig) -> list[ColumnInfo]:
        table_infos: list[TableInfoMySQL] = []
        column_infos: list[ColumnInfoMysql] = []
        # 配置文件中有表信息
        for table in meta_config.tables:
            table_info = TableInfo(
                id=table.name,
                name=table.name,
                role=table.role,
                description=table.description,
            )
            table_infos.append(table_info)
            columns_types = await self.dw_mysql_repository.get_column_types(table.name)
            for column in table.columns:
                column_values = await self.dw_mysql_repository.get_column_values(column.name, table.name)
                column_info = ColumnInfo(
                    id=f"{table.name}.{column.name}",
                    name=column.name,
                    type=columns_types[column.name],
                    role=column.role,
                    examples=column_values,
                    description=column.description,
                    alias=column.alias,
                    table_id=table.name,
                )
                column_infos.append(column_info)
        # 保存表信息和列信息
        async with self.meta_mysql_repository.session.begin():
            self.meta_mysql_repository.save_table_infos(table_infos)
            self.meta_mysql_repository.save_column_values(column_infos)

        return column_infos

    async def _save_column_info_to_qdrant(self, column_infos: list[ColumnInfo]):
        await self.column_qdrant_repository.ensure_collection()
        points: list[dict] = []
        for column_infor in column_infos:
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": column_infor.name,
                    "payload": column_infor,
                }
            )
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": column_infor.description,
                    "payload": column_infor,
                }
            )
        for alia in column_infor.alias:
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": alia,
                    "payload": column_infor,
                }
            )
        # 向量列表
        # 提取所有待向量化文本
        embedding_texts = [point["embedding_text"] for point in points]
        # 每批处理10条
        embedding_batch_size = 10
        embeddings = []
        # 步长10分片遍历
        for i in range(0, len(embedding_texts), embedding_batch_size):
            # 截取当前批次文本
            batch_embedding_texts = embedding_texts[i: i + embedding_batch_size]
            # 异步批量生成向量
            batch_embeddings = await self.embedding_client.aembed_documents(
                batch_embedding_texts
            )
            # 合并到总向量列表
            embeddings.extend(batch_embeddings)

        # id列表
        ids = [point["id"] for point in points]
        # paylaod列表
        payloads = [point["payload"] for point in points]
        # 保存数据到qdrant
        await self.column_qdrant_repository.upsert(ids, embeddings, payloads)

    async def _save_value_info_to_es(self, meta_config: MetaConfig, column_infos: list[ColumnInfo]):
        await self.value_es_repository.ensure_index()

        value_infos: list[ValueInfo] = []
        for table in meta_config.tables:
            for column in table.columns:
                if column.sync:
                    # 查询字段取值
                    current_column_values = await self.dw_mysql_repository.get_column_values(column.name, table.name,
                                                                                             100000)
                    current_values_infos = [
                        ValueInfo(id=f"{table.name}.{column.name}.{current_column_value}", value=current_column_value,
                                  column_id=f"{table.name}.{column.name}") for current_column_value in
                        current_column_values]
                    value_infos.extend(current_values_infos)
        await self.value_es_repository.index(value_infos)

    async def _save_metrics_to_meta_db(self, meta_config):
        metric_infos: list[MetricInfo] = []
        column_metrics: list[ColumnMetric] = []
        for metric in meta_config.metrics:
            # 构造MetricInfo数据
            metric_info = MetricInfo(
                id=metric.name,
                name=metric.name,
                description=metric.description,
                relevant_columns=metric.relevant_columns,
                alias=metric.alias,
            )
            metric_infos.append(metric_info)
        for relevant_column in metric.relevant_columns:
            # 构造ColumnMetric数据
            column_metric = ColumnMetric(
                column_id=relevant_column, metric_id=metric.name
            )
            column_metrics.append(column_metric)
        # 保存到元数据数据库
        async with self.meta_mysql_repository.session.begin():
            await self.meta_mysql_repository.save_metric_infos(metric_infos)
            await self.meta_mysql_repository.save_column_metrics(column_metrics)

        return metric_infos

    async def _save_metric_info_to_qdrant(self, metric_infos: list[MetricInfo]):
        # 确保collection存在
        await self.metric_qdrant_repository.ensure_collection()

        # 构造待保存的数据
        points: list[dict] = []
        for metric_info in metric_infos:
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": metric_info.name,
                    "payload": metric_info,
                }
            )
            points.append(
                {
                    "id": uuid.uuid4(),
                    "embedding_text": metric_info.description,
                    "payload": metric_info,
                }
            )
            for alia in metric_info.alias:
                points.append(
                    {"id": uuid.uuid4(), "embedding_text": alia, "payload": metric_info}
                )

        ids = [point["id"] for point in points]
        embeddings = []
        embedding_texts = [point["embedding_text"] for point in points]
        embedding_batch_size = 10
        for i in range(0, len(embedding_texts), embedding_batch_size):
            batch_embedding_texts = embedding_texts[i: i + embedding_batch_size]
            batch_embeddings = await self.embedding_client.aembed_documents(
                batch_embedding_texts
            )
            embeddings.extend(batch_embeddings)
        payloads = [point["payload"] for point in points]

        # 保存数据到qdrant
        await self.metric_qdrant_repository.upsert(ids, embeddings, payloads)

    async def build(self, config_path: Path):
        # 1.读取配置文件
        context = OmegaConf.load(config_path)
        schema = OmegaConf.structured(MetaConfig)
        meta_config: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))
        logger.info("加载配置文件")
        # 2.根据配置文件同步指定的表信息和指标信息
        if meta_config.tables:
            # 保存表信息到meta数据库
            column_infos = await self._save_tables_to_meta_db(meta_config)
            logger.info("保存表信息到meta数据库")

            # 对字段信息创建向量索引
            await self._save_column_info_to_qdrant(column_infos)
            logger.info("为字段信息建立向量索引")

            # 对指定的维度字段建立全文索引
            await self._save_value_info_to_es(meta_config,column_infos)
            logger.info("为字段取值建立全文索引")

        if meta_config.metrics:
            # 将指标信息保存到meta数据库中
            metric_infos = await self._save_metrics_to_meta_db(meta_config)
            logger.info("保存指标信息到meta数据库")

            # 对指标信息建立向量索引
            await self._save_metric_info_to_qdrant(metric_infos)
            logger.info("为指标信息建立向量索引")
        logger.info("元数据知识库构建完成")
