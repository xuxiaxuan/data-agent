"""评测数据集生成脚本

用法：
    python -m app.scripts.gen_eval_dataset

可选参数：
    -o, --output PATH   输出路径（默认 eval/datasets/text2sql_eval.jsonl）
    --no-self-check     跳过 ground_truth SQL 自检（默认开启）

流程：
1. 加载 SEED_SAMPLES（33 条）
2. 自检每条 ground_truth SQL 在 dw 库可执行（失败不中断，仅告警）
3. 写入 JSONL
"""
import argparse
import asyncio
from pathlib import Path

from app.clients.mysql_client_manager import dw_mysql_client_manager
from app.core.log import logger
from app.eval.dataset_generator import SEED_SAMPLES, self_check_sql, write_jsonl
from app.repositories.mysql.dw.dw_mysql_repository import DwMysqlRepository

PROJECT_ROOT = Path(__file__).parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "eval" / "datasets" / "text2sql_eval.jsonl"


async def generate(output: Path, self_check: bool) -> int:
    """生成评测集。返回自检失败条数。"""
    samples = [s.to_dict() for s in SEED_SAMPLES]
    failures = 0

    if self_check:
        logger.info("开始 ground_truth SQL 自检（在 dw 库执行）...")
        dw_mysql_client_manager.init()
        try:
            async with dw_mysql_client_manager.session_factory() as session:
                dw_repository = DwMysqlRepository(session)
                for sample in samples:
                    ok, err = await self_check_sql(
                        dw_repository, sample["ground_truth"]["sql"]
                    )
                    if ok:
                        logger.info(f"  [OK]   {sample['id']}  {sample['query']}")
                    else:
                        failures += 1
                        logger.error(
                            f"  [FAIL] {sample['id']}  {sample['query']}  -> {err}"
                        )
        finally:
            await dw_mysql_client_manager.close()
        logger.info(f"自检完成：{len(samples) - failures}/{len(samples)} 通过")
    else:
        logger.info("已跳过 ground_truth SQL 自检")

    write_jsonl(samples, output)
    return failures


def main():
    parser = argparse.ArgumentParser(description="生成 Text-to-SQL 评测数据集")
    parser.add_argument(
        "-o", "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"输出 JSONL 路径（默认 {DEFAULT_OUTPUT}）",
    )
    parser.add_argument(
        "--no-self-check",
        action="store_true",
        help="跳过 ground_truth SQL 在 dw 库的自检",
    )
    args = parser.parse_args()

    failures = asyncio.run(generate(Path(args.output), self_check=not args.no_self_check))
    if failures > 0:
        logger.warning(f"有 {failures} 条 ground_truth SQL 自检失败，请检查")
        # 失败不返回非零，便于 CI 容忍；具体失败已在日志体现
    logger.info("数据集生成完成")


if __name__ == "__main__":
    main()
