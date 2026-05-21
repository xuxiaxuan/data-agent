from pathlib import Path

from omegaconf import OmegaConf

from app.conf.meta_config import MetaConfig
from app.models.column_info_mysql import ColumnInfoMysql
from app.models.table_info_mysql import TableInfoMySQL
from app.repositories.mysql.dw import dw_mysql_repository
from app.repositories.mysql.dw.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMysqlRepository


class MetaKnowledgeService:
    def __init__(self, meta_mysql_repository: MetaMysqlRepository, dw_mysql_repository: DwMysqlRepository):
        self.meta_mysql_repository = meta_mysql_repository
        self.dw_mysql_repository = dw_mysql_repository

    async def build(self, config_path: Path):
        # 1.读取配置文件
        context = OmegaConf.load(config_path)
        schema = OmegaConf.structured(MetaConfig)
        meta_config: MetaConfig = OmegaConf.to_object(OmegaConf.merge(schema, context))
        print(meta_config.metrics)
        # 2.根据配置文件同步指定的表信息和指标信息
        if meta_config.tables:
            table_infos : list[TableInfoMySQL] = []
            column_infors : list[ColumnInfoMysql] = []
            # 配置文件中有表信息
            for table in meta_config.tables:
                table_infor = TableInfoMySQL(
                    id = table.name,
                    name = table.name,
                    description = table.description,
                )
                table_infos.append(table_infor)
                columns_types = await self.dw_mysql_repository.get_column_types(table.name)
                for column in table.columns:
                    column_values = await self.dw_mysql_repository.get_column_values(column.name, table.name)
                    column_infor = ColumnInfoMysql(
                        id = f"{table.name}.{column.name}",
                        name = column.name,
                        type = columns_types[column.name],
                        role = column.role,
                        examples = column_values,
                        description = column.description,
                        alias = column.alias,
                        table_id = table.name,
                    )
                    column_infors.append(column_infor)
            # 同步表信息
            print(table_infos)
            print("="*100)
            print(column_infors)

        if meta_config.metrics:
            # 配置文件中有指标信息
            for metric in meta_config.metrics:
                pass
            # 同步指标信息