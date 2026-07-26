"""RAGAS 评测运行脚本

用法：
    # 完整评测
    python -m app.scripts.run_ragas_eval

    # 冒烟测试（仅前 3 条，跳过 RAGAS LLM）
    python -m app.scripts.run_ragas_eval --limit 3 --skip-ragas

    # 指定数据集
    python -m app.scripts.run_ragas_eval --dataset eval/datasets/text2sql_eval.jsonl

风格：参照现有 build_meta_knowledge.py（argparse + asyncio + 手动 init/close）
"""
import argparse
import asyncio
import json
from pathlib import Path

from app.core.log import logger
from app.eval.evaluator import run_evaluation

PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_DATASET = PROJECT_ROOT / "eval" / "datasets" / "text2sql_eval.jsonl"
DEFAULT_RESULT_DIR = PROJECT_ROOT / "eval" / "results"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "eval" / "reports"


def main():
    parser = argparse.ArgumentParser(description="运行 Text-to-SQL RAGAS 评测")
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help=f"评测集 JSONL 路径（默认 {DEFAULT_DATASET}）",
    )
    parser.add_argument(
        "--result-dir",
        default=str(DEFAULT_RESULT_DIR),
        help=f"明细输出目录（默认 {DEFAULT_RESULT_DIR}）",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help=f"报告输出目录（默认 {DEFAULT_REPORT_DIR}）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="仅评测前 N 条样本（冒烟测试用）",
    )
    parser.add_argument(
        "--skip-ragas",
        action="store_true",
        help="跳过 RAGAS LLM 评测（仅跑 strict 指标 + SQL EX/VSR/CSR）",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(
            f"评测集不存在：{dataset_path}。请先运行 python -m app.scripts.gen_eval_dataset"
        )

    summary = asyncio.run(
        run_evaluation(
            dataset_path=dataset_path,
            result_dir=Path(args.result_dir),
            report_dir=Path(args.report_dir),
            skip_ragas=args.skip_ragas,
            limit=args.limit,
        )
    )

    logger.info("=== 评测完成 ===")
    csr = summary["csr"]
    if csr.get("csr") is None:
        csr_log = "N/A (无失败样本)"
    else:
        csr_log = (
            f"{csr['csr']:.4f} "
            f"(失败 {csr['failed_count']}/真纠错 {csr['correction_attempted']}/"
            f"摆烂 {csr['correction_lazy']}/救回 {csr['success_count']})"
        )
    logger.info(f"VSR={summary['vsr']:.4f}  CSR={csr_log}")
    logger.info("Overall:")
    for k, v in summary["overall"].items():
        if k.endswith("__valid"):
            continue  # 跳过辅助计数字段
        if v is None:
            logger.info(f"  {k}: N/A")
        elif isinstance(v, bool):
            logger.info(f"  {k}: {v}")
        elif isinstance(v, float):
            logger.info(f"  {k}: {v:.4f}")
        else:
            logger.info(f"  {k}: {v}")
    logger.info(f"明细：{summary['raw_path']}")
    logger.info(f"报告：{summary['report_path']}")


if __name__ == "__main__":
    main()
