"""评测主入口：组装流程 + 分组聚合 + 报告生成

流程：
1. 加载 conf/eval_config.yaml + 评测数据集 JSONL
2. 初始化客户端管理器（embedding/qdrant/es/meta_mysql/dw_mysql）
3. 逐条样本（concurrency=1）：
   a. 创建独立 session（防止 validate_sql 失败后 session 中毒）
   b. 零侵入执行 graph，捕获中间产物
   c. 计算 SQL 指标（EX/VSR/CSR）+ 召回指标（RAGAS + strict）
4. 全局 + 按 category 分组聚合
5. 输出 raw_<ts>.jsonl + report_<ts>.md
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from app.agent.context import DataAgentContext
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.log import logger
from app.eval.graph_runner import run_graph_and_collect
from app.eval.llm_wrapper import build_eval_embeddings, build_eval_llm
from app.eval.metrics.recall_metrics import (
    score_columns_ragas,
    score_columns_strict,
    score_metrics_ragas,
    score_metrics_strict,
)
from app.eval.metrics.sql_metrics import (
    aggregate_by_category,
    correction_success_rate,
    execution_accuracy,
    overall_mean,
    valid_syntax_rate,
)
from app.repositories.es.value_es_repository import ValueEsRepository
from app.repositories.mysql.dw.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMysqlRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

PROJECT_ROOT = Path(__file__).parents[2]
EVAL_CONFIG_PATH = PROJECT_ROOT / "conf" / "eval_config.yaml"

# 所有需要落盘 + 参与聚合的指标 key
METRIC_KEYS = [
    # SQL 类
    "ex",
    # 召回 strict 类
    "strict_column_recall",
    "strict_column_precision",
    "strict_metric_recall",
    "strict_metric_precision",
    # 召回 RAGAS 类（LLM 评分，-1 表示评分失败，聚合时过滤）
    "ragas_column_context_precision",
    "ragas_column_context_recall",
    "ragas_metric_context_precision",
    "ragas_metric_context_recall",
    # 召回数量诊断类（不参与 0~1 评分，但落盘 + 展示）
    "retrieved_column_count",
    "gt_column_count",
    "retrieved_metric_count",
    "gt_metric_count",
]

# 仅落盘但不参与均值聚合的 key（数量类指标，做均值无意义）
# 用 tuple 而非 set，保证报告里列顺序稳定
DIAGNOSTIC_KEYS = (
    "retrieved_column_count",
    "gt_column_count",
    "retrieved_metric_count",
    "gt_metric_count",
)

# 指标中文名 + 含义说明（用于报告可读性）
METRIC_DISPLAY: dict[str, tuple[str, str]] = {
    "ex": (
        "SQL 执行准确率",
        "生成的 SQL 在数据库执行的结果与标准答案 SQL 结果一致的比例（核心指标，0~1，越高越好）",
    ),
    "strict_column_recall": (
        "字段召回率·严格",
        "标准答案需要的字段中，被 Agent 召回的比例（确定性基线，0~1，越高越好；N/A 表示该题无字段需求）",
    ),
    "strict_column_precision": (
        "字段精度·严格",
        "Agent 召回的字段中，属于标准答案的比例（避免召回过多无关字段，0~1，越高越好）",
    ),
    "strict_metric_recall": (
        "指标召回率·严格",
        "标准答案需要的业务指标（如 GMV/AOV）中，被 Agent 召回的比例（0~1；N/A 表示该题无指标需求）",
    ),
    "strict_metric_precision": (
        "指标精度·严格",
        "Agent 召回的业务指标中，属于标准答案的比例（0~1；若该题无指标需求而 Agent 召回了指标，此项为 0 表示过度召回）",
    ),
    "ragas_column_context_precision": (
        "字段精度·RAGAS",
        "GLM-5.2 评估 Agent 召回字段与问题的相关程度（LLM 语义评分，0~1；N/A 表示该题无字段需求）",
    ),
    "ragas_column_context_recall": (
        "字段召回率·RAGAS",
        "GLM-5.2 评估标准答案字段被 Agent 召回覆盖的程度（LLM 语义评分，0~1）",
    ),
    "ragas_metric_context_precision": (
        "指标精度·RAGAS",
        "GLM-5.2 评估召回指标与问题的相关程度（0~1）",
    ),
    "ragas_metric_context_recall": (
        "指标召回率·RAGAS",
        "GLM-5.2 评估标准指标被召回覆盖的程度（0~1）",
    ),
    "retrieved_column_count": (
        "字段召回总数",
        "Agent 召回的字段数量（诊断字段：用于判断是召回过少还是召回过多导致 precision/recall 异常）",
    ),
    "gt_column_count": (
        "字段需求总数",
        "该题标准答案需要的字段数量",
    ),
    "retrieved_metric_count": (
        "指标召回总数",
        "Agent 召回的业务指标数量",
    ),
    "gt_metric_count": (
        "指标需求总数",
        "该题标准答案需要的业务指标数量",
    ),
}

# VSR / CSR 单独展示（基于 snapshot 计算，不在 METRIC_KEYS 里）
VSR_DISPLAY = ("SQL 语法一次通过率 (VSR)", "Agent 第一次生成的 SQL 通过语法校验的比例（不需要纠错节点介入，0~1，越高越好）")
CSR_DISPLAY = ("纠错成功率 (CSR)", "首次校验失败的样本中，纠错节点救回最终执行成功的比例（N/A 表示无失败样本）。同时展示 attempted/lazy 分类，识别纠错节点是否真正介入。")

# 样本分类中文名
CATEGORY_DISPLAY: dict[str, str] = {
    "single_table": "A.单表查询",
    "single_dim_group": "B.单维度分组",
    "multi_dim": "C.多维交叉",
    "metric": "D.指标查询(GMV/AOV)",
    "filter": "E.条件过滤",
    "topn": "F.排序TopN",
    "edge_case": "G.边界异常",
}


def metric_label(key: str) -> str:
    """取指标中文标签（无映射则原样返回）。"""
    return METRIC_DISPLAY.get(key, (key, ""))[0]


def category_label(key: str) -> str:
    """取分类中文标签（无映射则原样返回）。"""
    return CATEGORY_DISPLAY.get(key, key)


# ---------------------------------------------------------------------------
# 加载与序列化
# ---------------------------------------------------------------------------

def load_eval_config() -> dict:
    """加载 conf/eval_config.yaml。"""
    return OmegaConf.to_container(OmegaConf.load(EVAL_CONFIG_PATH), resolve=True)  # type: ignore


def load_dataset(path: Path) -> list[dict]:
    samples: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    logger.info(f"已加载评测集：{path}（{len(samples)} 条）")
    return samples


def write_raw_jsonl(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    logger.info(f"明细已写入：{path}")


def _fmt_score(v: Any, valid_count: float | None = None, *, is_count: bool = False) -> str:
    """格式化分数：None → N/A；计数类 → 整数；其他 → 4 位小数 + 可选 valid 标注。

    特别：若 valid_count 显式为 0（该指标在该分组无任何有效样本），统一显示 N/A。
    """
    if is_count:
        if v is None:
            return "0"
        return str(int(v))
    # valid_count === 0 表示该指标在该分组无任何有效样本，直接显示 N/A
    if valid_count is not None and valid_count == 0:
        return "N/A"
    if v is None:
        return "N/A"
    if isinstance(v, (int, float)):
        if v < 0:
            return "N/A"
        s = f"{v:.4f}"
        if valid_count is not None and valid_count > 0:
            s += f" (n={int(valid_count)})"
        return s
    return str(v)


def write_report_md(
    results: list[dict],
    by_category: dict[str, dict[str, float]],
    overall: dict[str, float],
    vsr: float,
    csr: dict[str, Any],
    path: Path,
    dataset_path: Path,
) -> None:
    """生成 Markdown 评测报告（所有指标附中文名 + 含义说明 + N/A 处理）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Text-to-SQL Agent RAGAS 评测报告\n")
    lines.append(f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 数据集：`{dataset_path}`")
    lines.append(f"- 样本总数：{len(results)}")
    lines.append("")

    # 指标词典
    lines.append("## 指标说明\n")
    lines.append("| 指标 | 中文名 | 含义 |")
    lines.append("|------|--------|------|")
    lines.append(f"| VSR | {VSR_DISPLAY[0]} | {VSR_DISPLAY[1]} |")
    lines.append(f"| CSR | {CSR_DISPLAY[0]} | {CSR_DISPLAY[1]} |")
    for k in METRIC_KEYS:
        cn_name, desc = METRIC_DISPLAY.get(k, (k, ""))
        lines.append(f"| `{k}` | {cn_name} | {desc} |")
    lines.append("")
    lines.append("> 阅读提示：")
    lines.append("> - `N/A` 表示该题无可比项（如 COUNT(*) 不需特定字段）或该项被跳过（如 `--skip-ragas`）。")
    lines.append("> - 全局/分组均值后 `n=X` 表示参与该指标聚合的有效样本数。")
    lines.append("> - `strict_*` 是基于字段名精确比对的确定性基线；`ragas_*` 是 GLM-5.2 的语义评分。\n")

    # 全局指标
    lines.append("## 全局指标\n")
    lines.append("| 指标 | 中文名 | 取值 |")
    lines.append("|------|--------|------|")
    lines.append(f"| VSR | {VSR_DISPLAY[0]} | {vsr:.4f} (n={len(results)}) |")
    csr_val = csr.get("csr")
    lines.append(
        f"| CSR | {CSR_DISPLAY[0]} | "
        f"{'N/A' if csr_val is None else f'{csr_val:.4f}'} "
        f"(失败 {csr.get('failed_count', 0)} / 真纠错 {csr.get('correction_attempted', 0)} / 摆烂 {csr.get('correction_lazy', 0)} / 救回 {csr.get('success_count', 0)}) |"
    )
    for k in METRIC_KEYS:
        v = overall.get(k)
        valid = overall.get(f"{k}__valid") if k not in DIAGNOSTIC_KEYS else None
        is_count = k in DIAGNOSTIC_KEYS
        lines.append(f"| `{k}` | {metric_label(k)} | {_fmt_score(v, valid, is_count=is_count)} |")
    lines.append("")

    # 按 category 分组
    lines.append("## 按样本类型分组（定位薄弱环节）\n")
    # 分两个表：主指标表（0~1 评分）+ 诊断字段表（数量类）
    score_keys = [k for k in METRIC_KEYS if k not in DIAGNOSTIC_KEYS]
    # 主指标表
    lines.append("### 主指标（0~1，越高越好）\n")
    header_labels = ["样本数"] + [metric_label(k) for k in score_keys]
    lines.append("| 类型 | " + " | ".join(header_labels) + " |")
    lines.append("|" + "|".join(["---"] * (len(score_keys) + 2)) + "|")
    cat_order = list(CATEGORY_DISPLAY.keys())
    sorted_cats = sorted(
        by_category.items(),
        key=lambda kv: cat_order.index(kv[0]) if kv[0] in cat_order else 999,
    )
    for cat, metrics in sorted_cats:
        row = [category_label(cat), str(int(metrics.get("count", 0)))]
        for k in score_keys:
            v = metrics.get(k)
            valid = metrics.get(f"{k}__valid")
            row.append(_fmt_score(v, valid))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # 诊断字段表
    lines.append("### 召回数量诊断（绝对数，定位过召回/漏召回）\n")
    diag_labels = [metric_label(k) for k in DIAGNOSTIC_KEYS]
    lines.append("| 类型 | " + " | ".join(diag_labels) + " |")
    lines.append("|" + "|".join(["---"] * (len(DIAGNOSTIC_KEYS) + 2)) + "|")
    for cat, metrics in sorted_cats:
        row = [category_label(cat)]
        for k in DIAGNOSTIC_KEYS:
            row.append(_fmt_score(metrics.get(k), is_count=True))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # 已知问题标注
    lines.append("## 已知问题\n")
    lines.append("- `meta_config.yaml:175` 中 AOV 的 `relevant_columns` 误写为 `fact_order.order_quantity`，")
    lines.append("  本评测集 ground_truth 按 AOV 真实语义（`AVG(fact_order.order_amount)`）标注；")
    lines.append("  该字段召回评测对 AOV 样本（D03/D04）可能受此配置 bug 影响。")
    lines.append("- 业务代码零改动：本次评测未修改 `app/agent` `app/services` `app/repositories` `app/clients` `app/api` `main.py`。")

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"报告已写入：{path}")


# ---------------------------------------------------------------------------
# 单样本评测
# ---------------------------------------------------------------------------

async def evaluate_one(
    sample: dict,
    context: DataAgentContext,
    dw_repository: DwMysqlRepository,
    *,
    skip_ragas: bool = False,
) -> dict:
    """对单条样本跑完整评测。返回 sample 增强后的 dict（含 snapshot 与所有指标）。"""
    query = sample["query"]
    gt = sample["ground_truth"]

    snapshot = await run_graph_and_collect(query, context)

    result: dict[str, Any] = dict(sample)
    result["snapshot"] = {
        k: (
            [str(x) if not isinstance(x, (str, int, float, bool, type(None))) else x for x in v]
            if isinstance(v, list) else v
        )
        for k, v in snapshot.items()
    }

    # SQL 指标
    is_match, ex_err = await execution_accuracy(
        snapshot.get("sql"), gt["sql"], dw_repository
    )
    result["ex"] = 1.0 if is_match else 0.0
    result["ex_error"] = ex_err

    # 召回 strict 指标
    result.update(score_columns_strict(snapshot["retrieved_columns"], gt.get("columns", [])))
    result.update(score_metrics_strict(snapshot["retrieved_metrics"], gt.get("metrics", [])))

    # 召回 RAGAS 指标
    if not skip_ragas:
        try:
            from app.eval._ragas_compat import ensure_ragas_compat
            ensure_ragas_compat()

            from ragas.embeddings import LangchainEmbeddingsWrapper
            from app.eval.llm_wrapper import build_eval_llm_wrapper

            llm = build_eval_llm_wrapper()
            embeddings = LangchainEmbeddingsWrapper(build_eval_embeddings())

            col_scores = await score_columns_ragas(
                query, snapshot["retrieved_columns"], gt.get("columns", []),
                llm, embeddings,
            )
            met_scores = await score_metrics_ragas(
                query, snapshot["retrieved_metrics"], gt.get("metrics", []),
                llm, embeddings,
            )
            result.update(col_scores)
            result.update(met_scores)
        except Exception as e:
            logger.warning(f"RAGAS 评测失败（query={query}）：{e}")
            for k in [
                "ragas_column_context_precision", "ragas_column_context_recall",
                "ragas_metric_context_precision", "ragas_metric_context_recall",
            ]:
                result.setdefault(k, None)  # 评测失败用 None 标记，聚合时跳过
    else:
        for k in [
            "ragas_column_context_precision", "ragas_column_context_recall",
            "ragas_metric_context_precision", "ragas_metric_context_recall",
        ]:
            result.setdefault(k, None)  # 跳过 RAGAS 时用 None 标记

    # 简短日志（strict_*_recall 可能为 None，需要兜底格式化）
    def _fmt(v: Any) -> str:
        return "N/A" if v is None else f"{v:.2f}"

    logger.info(
        f"[{sample['id']}] {query}  EX={result['ex']:.0f}  "
        f"strict_col_recall={_fmt(result['strict_column_recall'])}  "
        f"strict_met_recall={_fmt(result['strict_metric_recall'])}  "
        f"sql={(snapshot.get('sql') or '')[:80]}"
    )
    return result


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run_evaluation(
    dataset_path: Path,
    result_dir: Path,
    report_dir: Path,
    *,
    skip_ragas: bool = False,
    limit: int | None = None,
) -> dict:
    """运行完整评测。返回 overall + by_category 指标 dict。"""
    config = load_eval_config()
    logger.info(f"评测配置：{json.dumps(config, ensure_ascii=False)}")

    samples = load_dataset(dataset_path)
    if limit:
        samples = samples[:limit]
        logger.info(f"按 --limit={limit} 截断，本次评测 {len(samples)} 条")

    # 初始化所有客户端（与 build_meta_knowledge.py 风格一致）
    embedding_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    results: list[dict] = []
    try:
        for idx, sample in enumerate(samples, 1):
            logger.info(f"=== [{idx}/{len(samples)}] {sample['id']} ===")
            # 每条样本独立 session：防止 validate_sql 失败后 session 中毒
            async with (
                meta_mysql_client_manager.session_factory() as meta_session,
                dw_mysql_client_manager.session_factory() as dw_session,
            ):
                context = DataAgentContext(
                    embedding_client=embedding_client_manager.client,
                    column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
                    value_es_repository=ValueEsRepository(es_client_manager.client),
                    metric_qdrant_repository=MetricQdrantRepository(qdrant_client_manager.client),
                    meta_mysql_repository=MetaMysqlRepository(meta_session),
                    dw_mysql_repository=DwMysqlRepository(dw_session),
                )
                # EX 复用同一样本的 dw_repository（仅读，与 graph 共享 session 不会写脏）
                result = await evaluate_one(
                    sample, context, context["dw_mysql_repository"],
                    skip_ragas=skip_ragas,
                )
                results.append(result)
    finally:
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()

    # 聚合
    vsr = valid_syntax_rate(
        [{"validation_error": r["snapshot"].get("validation_error")} for r in results]
    )
    csr = correction_success_rate(
        [
            {
                "validation_error": r["snapshot"].get("validation_error"),
                "result": r["snapshot"].get("result"),
                "stream_error": r["snapshot"].get("stream_error"),
                "generated_sql": r["snapshot"].get("generated_sql"),
                "corrected_sql": r["snapshot"].get("corrected_sql"),
            }
            for r in results
        ]
    )
    # 数量类指标（retrieved_*_count 等）做总和而非均值
    overall = overall_mean(results, [k for k in METRIC_KEYS if k not in DIAGNOSTIC_KEYS])
    # 诊断类单独求和（展示总数）
    for dk in DIAGNOSTIC_KEYS:
        values = [r.get(dk) for r in results if isinstance(r.get(dk), (int, float))]
        overall[dk] = sum(values) if values else 0
    by_category = aggregate_by_category(
        results, [k for k in METRIC_KEYS if k not in DIAGNOSTIC_KEYS]
    )
    # 分组也加上诊断类总和
    groups: dict[str, list[dict]] = {}
    for r in results:
        groups.setdefault(r.get("category", "unknown"), []).append(r)
    for cat, group in groups.items():
        for dk in DIAGNOSTIC_KEYS:
            values = [g.get(dk) for g in group if isinstance(g.get(dk), (int, float))]
            by_category.setdefault(cat, {})[dk] = sum(values) if values else 0

    # 落盘
    ts = int(time.time())
    raw_path = result_dir / f"raw_{ts}.jsonl"
    report_path = report_dir / f"report_{ts}.md"
    write_raw_jsonl(results, raw_path)
    write_report_md(
        results, by_category, overall, vsr, csr, report_path, dataset_path,
    )

    return {
        "overall": overall,
        "by_category": by_category,
        "vsr": vsr,
        "csr": csr,
        "raw_path": str(raw_path),
        "report_path": str(report_path),
    }
