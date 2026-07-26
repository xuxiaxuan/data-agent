"""零侵入 graph 执行 + 中间产物捕获

核心思路：
- graph.astream 支持 stream_mode=["updates", "custom"] 双流模式
- updates：每个节点完成后的 state 增量
- custom：节点内通过 runtime.stream_writer 输出的业务事件（如 execute_sql 的最终结果集）
- 不修改任何业务代码（app/agent/* 不变）
"""
from __future__ import annotations

from typing import Any

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.core.log import logger


def build_context(
    *,
    meta_mysql_repository,
    dw_mysql_repository,
    column_qdrant_repository,
    value_es_repository,
    metric_qdrant_repository,
    embedding_client,
) -> DataAgentContext:
    """构造 DataAgentContext，参数与 graph.py 内部 test() 保持一致。"""
    return DataAgentContext(
        embedding_client=embedding_client,
        column_qdrant_repository=column_qdrant_repository,
        value_es_repository=value_es_repository,
        metric_qdrant_repository=metric_qdrant_repository,
        meta_mysql_repository=meta_mysql_repository,
        dw_mysql_repository=dw_mysql_repository,
    )


async def run_graph_and_collect(query: str, context: DataAgentContext) -> dict[str, Any]:
    """执行 graph 并收集所有节点中间产物。

    Returns:
        {
            "retrieved_columns":   list[ColumnInfo],   # recall_column 节点输出
            "retrieved_metrics":   list[MetricInfo],   # recall_metric 节点输出
            "table_infos_after_filter": list,          # filter_table 节点输出
            "sql":                 str | None,         # 最终 SQL（generate 或 correct 覆盖后的）
            "generated_sql":       str | None,         # 首次生成的 SQL
            "validation_error":    str | None,         # validate_sql 节点 error 字段
            "corrected_sql":       str | None,         # 纠错后的 SQL（若进入纠错分支）
            "result":              Any,                # execute_sql custom 事件结果
            "stream_error":        str | None,         # 整体执行异常
        }
    """
    snapshots: dict[str, dict] = {}
    final_result: Any = None
    stream_error: str | None = None

    try:
        async for chunk in graph.astream(
            input=DataAgentState(query=query),
            context=context,
            stream_mode=["updates", "custom"],
        ):
            # 多 stream_mode 下 chunk 是 (mode, payload)
            mode, payload = chunk[0], chunk[1]

            if mode == "updates" and isinstance(payload, dict):
                for node, update in payload.items():
                    if isinstance(update, dict):
                        snapshots[node] = update

            elif mode == "custom" and isinstance(payload, dict):
                p_type = payload.get("type")
                if p_type == "result":
                    final_result = payload.get("data")
                elif p_type == "error":
                    final_result = {"_error": payload.get("message") or payload.get("error")}

    except Exception as e:
        # execute_sql 失败会 raise，捕获后记录为 stream_error
        stream_error = str(e)
        logger.error(f"graph 执行异常（query={query}）：{stream_error}")

    generated_sql = snapshots.get("generate_sql", {}).get("sql")
    corrected_sql = snapshots.get("correct_sql", {}).get("sql")
    final_sql = corrected_sql or generated_sql

    return {
        "retrieved_columns": snapshots.get("recall_column", {}).get("retrieved_columns", []) or [],
        "retrieved_metrics": snapshots.get("recall_metric", {}).get("retrieved_metrics", []) or [],
        "table_infos_after_filter": snapshots.get("filter_table", {}).get("table_infos", []) or [],
        "generated_sql": generated_sql,
        "corrected_sql": corrected_sql,
        "sql": final_sql,
        "validation_error": snapshots.get("validate_sql", {}).get("error"),
        "result": final_result,
        "stream_error": stream_error,
    }
