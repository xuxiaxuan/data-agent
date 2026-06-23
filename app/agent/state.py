from typing import TypedDict

from app.entities.column_info import ColumnInfo


class DataAgentState(TypedDict):
    query: str
    error: str
    keywords: list[str]
    retrieved_columns: list[ColumnInfo]  # 召回的字段信息
