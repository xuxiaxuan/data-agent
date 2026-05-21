from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MetaMysqlRepository():
    def __init__(self, session: AsyncSession):
        self.session = session
