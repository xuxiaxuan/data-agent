from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.column_info import ColumnInfo
from app.entities.table_info import TableInfo
from app.models.column_info_mysql import ColumnInfoMysql
from app.models.table_info_mysql import TableInfoMySQL
from app.repositories.mysql.meta.mappers.column_info_mapper import ColumnInfoMapper
from app.repositories.mysql.meta.mappers.table_info_mapper import TableInfoMapper


class MetaMysqlRepository():
    def __init__(self, session: AsyncSession):
        self.session = session

    def save_table_infos(self, table_infos: list[TableInfo]):
        table_infos = [TableInfoMapper.to_model(table_info) for table_info in table_infos]
        self.session.add_all(table_infos)

    def save_column_values(self, column_infos: list[ColumnInfo]):
        column_infos = [ColumnInfoMapper.to_model(column_info) for column_info in column_infos]
        self.session.add_all(column_infos)
