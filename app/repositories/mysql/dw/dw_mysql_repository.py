from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.mysql.meta.meta_mysql_repository import MetaMysqlRepository


class DwMysqlRepository():
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_column_types(self, table_name) -> dict[str,str]:
        sql = f"show columns from {table_name}"
        result = await self.session.execute(text(sql))
        result_dict = result.fetchall()
        return {row.Field: row.Type for row in result_dict}

    async def get_column_values(self, column_name, table_name, limit=10):
        sql = f"select distinct {column_name} from {table_name} limit {limit}"
        result = await self.session.execute(text(sql))
        return [row[0] for row in result.fetchall()]

    async def get_db_info(self):
        result = await self.session.execute(text("select version()"))
        version = result.scalar()

        dialect = self.session.get_bind().dialect.name

        return {'version': version, 'dialect': dialect}

    async def validate_sql(self, sql):
        await self.session.execute(text(f"explain {sql}"))

    async def execute_sql(self, sql):
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]