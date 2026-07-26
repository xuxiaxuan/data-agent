"""召回评测指标

两套计算方式：
1. RAGAS 原生 LLM 指标（context_precision / LLMContextRecall）
   - 优点：语义级评估
   - 缺点：依赖评测 LLM，结果有随机性
2. 严格集合匹配（exact match on table.column）
   - 优点：确定性、对结构化字段召回最准确
   - 缺点：不会算"语义相近但写法不同"的字段

评测主入口（evaluator.py）会同时跑两套，最终报告里并列展示。
LLM 版作为主指标（符合方案），严格版作为基线参考。
"""
from __future__ import annotations

from typing import Any

from app.core.log import logger


# ---------------------------------------------------------------------------
# 序列化：ColumnInfo / MetricInfo → RAGAS context 字符串
# ---------------------------------------------------------------------------

def column_to_context(col: Any) -> str:
    """ColumnInfo → 字符串表示（用于 RAGAS retrieved_contexts）。"""
    # ColumnInfo 字段：id, name, type, role, examples, description, alias, table_id
    alias = ", ".join(getattr(col, "alias", []) or [])
    parts = [f"id={col.id}", f"name={col.name}"]
    if getattr(col, "table_id", None):
        parts.append(f"table={col.table_id}")
    if getattr(col, "role", None):
        parts.append(f"role={col.role}")
    if getattr(col, "description", None):
        parts.append(f"desc={col.description}")
    if alias:
        parts.append(f"alias={alias}")
    return " | ".join(parts)


def metric_to_context(metric: Any) -> str:
    """MetricInfo → 字符串表示（用于 RAGAS retrieved_contexts）。"""
    alias = ", ".join(getattr(metric, "alias", []) or [])
    parts = [f"name={metric.name}"]
    if getattr(metric, "description", None):
        parts.append(f"desc={metric.description}")
    if getattr(metric, "relevant_columns", None):
        parts.append(f"cols={metric.relevant_columns}")
    if alias:
        parts.append(f"alias={alias}")
    return " | ".join(parts)


def _columns_reference(ground_truth_columns: list[str]) -> str:
    """ground_truth 列 → reference 字符串（自然语言化以便 LLM 评估）。"""
    if not ground_truth_columns:
        return ""
    items = "; ".join(ground_truth_columns)
    return f"该查询需要的字段（table.column）：{items}"


def _metrics_reference(ground_truth_metrics: list[str]) -> str:
    if not ground_truth_metrics:
        return ""
    items = ", ".join(ground_truth_metrics)
    return f"该查询需要的指标：{items}"


# ---------------------------------------------------------------------------
# 严格集合匹配（不依赖 LLM）
# ---------------------------------------------------------------------------

def strict_recall(retrieved_keys: list[str], gt_keys: list[str]) -> float | None:
    """召回率：|retrieved ∩ gt| / |gt|。

    返回 None：GT 为空（无可比项，报告显示 N/A，不参与聚合）。
    """
    if not gt_keys:
        return None
    retrieved_set = {k.lower() for k in retrieved_keys}
    gt_set = {k.lower() for k in gt_keys}
    hit = len(retrieved_set & gt_set)
    return hit / len(gt_set)


def strict_precision(retrieved_keys: list[str], gt_keys: list[str]) -> float | None:
    """精度：|retrieved ∩ gt| / |retrieved|。

    返回 None：retrieved 与 GT 都为空（无可比项）。
    返回 0.0：retrieved 空但 GT 非空（漏召回），或 retrieved 非空但 GT 空（过度召回）。
    """
    if not retrieved_keys:
        return None if not gt_keys else 0.0
    retrieved_set = {k.lower() for k in retrieved_keys}
    gt_set = {k.lower() for k in gt_keys}
    hit = len(retrieved_set & gt_set)
    return hit / len(retrieved_set)


def score_columns_strict(
    retrieved_columns: list[Any], gt_columns: list[str]
) -> dict[str, Any]:
    """严格字段召回评分。同时输出召回总数 / GT 总数用于诊断。"""
    retrieved_keys = [c.id for c in retrieved_columns if getattr(c, "id", None)]
    return {
        "strict_column_recall": strict_recall(retrieved_keys, gt_columns),
        "strict_column_precision": strict_precision(retrieved_keys, gt_columns),
        "retrieved_column_count": len(retrieved_keys),
        "gt_column_count": len(gt_columns),
    }


def score_metrics_strict(
    retrieved_metrics: list[Any], gt_metrics: list[str]
) -> dict[str, Any]:
    """严格指标召回评分。同时输出召回总数 / GT 总数用于诊断。"""
    retrieved_keys = [m.name for m in retrieved_metrics if getattr(m, "name", None)]
    return {
        "strict_metric_recall": strict_recall(retrieved_keys, gt_metrics),
        "strict_metric_precision": strict_precision(retrieved_keys, gt_metrics),
        "retrieved_metric_count": len(retrieved_keys),
        "gt_metric_count": len(gt_metrics),
    }


# ---------------------------------------------------------------------------
# RAGAS LLM 评测（context_precision / LLMContextRecall）
# ---------------------------------------------------------------------------

async def _ragas_score_single(
    sample_dict: dict[str, Any],
    metrics_list: list[Any],
    llm_wrapper,
    embeddings_wrapper,
) -> dict[str, float]:
    """对单个样本跑 RAGAS 指标。返回 {metric_name: score}。

    任何异常都吞掉并返回 -1 标记，避免单条失败拖垮整体评测。
    """
    from ragas.dataset_schema import SingleTurnSample

    result: dict[str, float] = {}
    sample = SingleTurnSample(**sample_dict)

    for metric in metrics_list:
        metric_name = getattr(metric, "name", metric.__class__.__name__)
        try:
            # 注入 wrappers
            if hasattr(metric, "llm"):
                metric.llm = llm_wrapper
            if hasattr(metric, "embeddings"):
                metric.embeddings = embeddings_wrapper
            score = await metric.single_turn_ascore(sample)
            result[metric_name] = float(score)
        except Exception as e:
            logger.warning(f"RAGAS 指标 {metric_name} 评分失败：{e}")
            result[metric_name] = -1.0
    return result


async def score_columns_ragas(
    query: str,
    retrieved_columns: list[Any],
    gt_columns: list[str],
    llm_wrapper,
    embeddings_wrapper,
) -> dict[str, float]:
    """对字段召回跑 RAGAS context_precision / context_recall。"""
    if not retrieved_columns and not gt_columns:
        # 双空：无可比项，返回 None，报告显示 N/A
        return {"ragas_column_context_precision": None, "ragas_column_context_recall": None}

    try:
        from app.eval._ragas_compat import ensure_ragas_compat
        ensure_ragas_compat()
        from ragas.metrics import ContextPrecision, LLMContextRecall
    except ImportError as e:
        logger.warning(f"RAGAS 未安装，跳过 ragas 指标：{e}")
        return {}

    sample = {
        "user_input": query,
        "retrieved_contexts": [column_to_context(c) for c in retrieved_columns],
        "reference": _columns_reference(gt_columns),
    }
    scores = await _ragas_score_single(
        sample, [ContextPrecision(), LLMContextRecall()], llm_wrapper, embeddings_wrapper
    )
    return {f"ragas_column_{k}": v for k, v in scores.items()}


async def score_metrics_ragas(
    query: str,
    retrieved_metrics: list[Any],
    gt_metrics: list[str],
    llm_wrapper,
    embeddings_wrapper,
) -> dict[str, float]:
    """对指标召回跑 RAGAS context_precision / context_recall。"""
    if not retrieved_metrics and not gt_metrics:
        # 双空：无可比项，返回 None，报告显示 N/A
        return {"ragas_metric_context_precision": None, "ragas_metric_context_recall": None}

    try:
        from app.eval._ragas_compat import ensure_ragas_compat
        ensure_ragas_compat()
        from ragas.metrics import ContextPrecision, LLMContextRecall
    except ImportError as e:
        logger.warning(f"RAGAS 未安装，跳过 ragas 指标：{e}")
        return {}

    sample = {
        "user_input": query,
        "retrieved_contexts": [metric_to_context(m) for m in retrieved_metrics],
        "reference": _metrics_reference(gt_metrics),
    }
    scores = await _ragas_score_single(
        sample, [ContextPrecision(), LLMContextRecall()], llm_wrapper, embeddings_wrapper
    )
    return {f"ragas_metric_{k}": v for k, v in scores.items()}
