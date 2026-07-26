"""SQL 评测指标（自定义实现，不依赖 RAGAS）

三个核心指标：
- Execution Accuracy (EX)：预测 SQL 与 GT SQL 执行结果集一致（排序后比对）
- Valid Syntax Rate (VSR)：validate_sql 节点 error 为 None 的比例
- Correction Success Rate (CSR)：首次校验失败 → 纠错后执行成功 的比例

设计原则（KISS）：
- EX 比对：行集合相等（按行内值排序 + 行间排序，容忍列顺序差异）
- 浮点容差：FLOAT 列 SUM/AVG 保留 4 位小数比较
- 异常吞掉：执行失败一律视为 EX 不通过
"""
from __future__ import annotations

from typing import Any

from app.core.log import logger

FLOAT_TOLERANCE_DIGITS = 4  # 浮点保留 4 位小数后比较


# ---------------------------------------------------------------------------
# 单值 / 行 归一化（用于 EX 比对）
# ---------------------------------------------------------------------------

def _normalize_value(v: Any) -> Any:
    """归一化值：浮点四舍五入，None 保留，其它原样。"""
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, FLOAT_TOLERANCE_DIGITS)
    if isinstance(v, bool):
        return int(v)
    return v


def _normalize_row(row: dict) -> tuple:
    """行归一化：取所有 value，归一化后按值排序（容忍 SELECT 列顺序差异）。"""
    normalized = [_normalize_value(v) for v in row.values()]
    return tuple(sorted(normalized, key=lambda x: (x is None, str(x))))


def _rows_match(pred_rows: list[dict], gt_rows: list[dict]) -> bool:
    """结果集比对：行数相等 + 行集合相等（排序后比对）。"""
    if len(pred_rows) != len(gt_rows):
        return False
    pred_normalized = sorted(_normalize_row(r) for r in pred_rows)
    gt_normalized = sorted(_normalize_row(r) for r in gt_rows)
    return pred_normalized == gt_normalized


# ---------------------------------------------------------------------------
# Execution Accuracy
# ---------------------------------------------------------------------------

async def execution_accuracy(pred_sql: str | None, gt_sql: str, dw_repository) -> tuple[bool, str | None]:
    """执行两个 SQL，比对结果集。

    Returns:
        (is_match, error_message)
        - pred_sql 为 None/空 → (False, "empty predicted sql")
        - 执行异常 → (False, error_str)
        - 比对通过 → (True, None)
    """
    if not pred_sql or not pred_sql.strip():
        return False, "empty predicted sql"

    try:
        pred_rows = await dw_repository.execute_sql(pred_sql)
    except Exception as e:
        return False, f"pred exec error: {e}"

    try:
        gt_rows = await dw_repository.execute_sql(gt_sql)
    except Exception as e:
        # GT SQL 应该已经在数据集生成时自检通过；这里失败说明环境问题
        logger.error(f"ground_truth SQL 执行失败（数据集或环境问题）：{gt_sql} -> {e}")
        return False, f"gt exec error: {e}"

    return _rows_match(pred_rows, gt_rows), None


# ---------------------------------------------------------------------------
# VSR / CSR（基于 samples 列表，每条样本需含 validation_error / result）
# ---------------------------------------------------------------------------

def valid_syntax_rate(samples: list[dict]) -> float:
    """Valid Syntax Rate：validate_sql 节点 error 为 None 的比例。"""
    if not samples:
        return 0.0
    valid = sum(1 for s in samples if s.get("validation_error") is None)
    return valid / len(samples)


def correction_success_rate(samples: list[dict]) -> dict[str, Any]:
    """Correction Success Rate：纠错成功率 + 状态分类。

    Args:
        samples: 每条样本需含 validation_error / result / stream_error / generated_sql / corrected_sql

    Returns:
        {
            "csr": float | None,        # 纠错成功率（无失败样本时为 None）
            "failed_count": int,        # 首次校验失败的样本数（CSR 分母）
            "correction_attempted": int,# 纠错节点真正介入的样本数（corrected_sql != generated_sql）
            "correction_lazy": int,     # 纠错节点摆烂的样本数（corrected_sql == generated_sql，但触发了纠错分支）
            "success_count": int,       # 纠错后执行成功的样本数（CSR 分子）
        }
    """
    failed = [s for s in samples if s.get("validation_error") is not None]
    if not failed:
        return {
            "csr": None,
            "failed_count": 0,
            "correction_attempted": 0,
            "correction_lazy": 0,
            "success_count": 0,
        }

    success = 0
    attempted = 0
    lazy = 0
    for s in failed:
        gen = (s.get("generated_sql") or "").strip()
        cor = (s.get("corrected_sql") or "").strip()
        # 纠错节点是否真正修改了 SQL（corrected_sql 可能与 generated_sql 相同 = 摆烂）
        if cor and cor != gen:
            attempted += 1
        elif cor:
            # corrected_sql 非空但与 generated_sql 相同：纠错节点摆烂
            lazy += 1

        # 最终执行是否成功：result 非 _error 标记 且 stream_error 为空
        is_error_result = isinstance(s.get("result"), dict) and s["result"].get("_error")
        if not is_error_result and s.get("stream_error") is None:
            success += 1

    return {
        "csr": success / len(failed),
        "failed_count": len(failed),
        "correction_attempted": attempted,
        "correction_lazy": lazy,
        "success_count": success,
    }


# ---------------------------------------------------------------------------
# 聚合辅助
# ---------------------------------------------------------------------------

def _is_valid_score(v: Any) -> bool:
    """判断是否为有效评分：跳过 None（无可比项）和 -1（评分失败占位）。"""
    if v is None:
        return False
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v >= 0  # 过滤 -1 占位
    return False


# ---------------------------------------------------------------------------
# 分组聚合
# ---------------------------------------------------------------------------

def aggregate_by_category(
    samples: list[dict],
    metric_keys: list[str],
) -> dict[str, dict[str, float]]:
    """按 category 聚合指标均值。

    过滤规则：None（无可比项）和 -1（评分失败）不参与均值。
    输出额外字段：`{key}__valid` 表示该指标在该分组的有效样本数。
    """
    groups: dict[str, list[dict]] = {}
    for s in samples:
        cat = s.get("category", "unknown")
        groups.setdefault(cat, []).append(s)

    result: dict[str, dict[str, float]] = {}
    for cat, group in groups.items():
        result[cat] = {"count": float(len(group))}
        for key in metric_keys:
            values = [s.get(key) for s in group if _is_valid_score(s.get(key))]
            result[cat][key] = sum(values) / len(values) if values else 0.0
            result[cat][f"{key}__valid"] = float(len(values))
    return result


def overall_mean(samples: list[dict], metric_keys: list[str]) -> dict[str, float]:
    """全局均值。

    过滤规则：None 和 -1 不参与均值。
    输出额外字段：`{key}__valid` 表示全局有效样本数。
    """
    result: dict[str, float] = {}
    for key in metric_keys:
        values = [s.get(key) for s in samples if _is_valid_score(s.get(key))]
        result[key] = sum(values) / len(values) if values else 0.0
        result[f"{key}__valid"] = float(len(values))
    return result
