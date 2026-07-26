"""评测数据集生成器

设计：
- 数据边界严格基于 docker/mysql/dw.sql + conf/meta_config.yaml
- 33 条样本覆盖 7 个维度（单表/单维/多维/指标/过滤/排序/边界）
- 每条 ground_truth SQL 在 dw 库可执行（自检）
- AOV 按"平均订单金额"真实语义标注（fact_order.order_amount），
  不沿用 meta_config.yaml:175 误写的 order_quantity（已知配置 bug，仅标注不动业务）

数据边界速查（来自 dw.sql）：
- 时间：仅 2025 Q1（1/1 - 3/31，共 90 天），无 2024 / 去年 / 下半年 / 全年
- 地区：5 大区（华南/华东/西南/华北/华中）+ 6 省份
- 会员等级：青铜/白银/黄金/铂金（无钻石）
- 品类：手机数码/家用电器/鞋靴/服饰/食品饮料/休闲零食
- 品牌：15 个
- 度量：fact_order.order_quantity（销量）、fact_order.order_amount（金额）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.log import logger


@dataclass
class Sample:
    id: str
    query: str
    category: str  # single_table | single_dim_group | multi_dim | metric | filter | topn | edge_case
    joins: int
    tables_count: int
    tables: list[str]
    columns: list[str]  # ground_truth 召回字段，格式 "table.column"
    metrics: list[str]  # ground_truth 召回指标，如 ["GMV"]
    sql: str
    edge_case: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "query": self.query,
            "category": self.category,
            "complexity": {"joins": self.joins, "tables": self.tables_count},
            "ground_truth": {
                "tables": self.tables,
                "columns": self.columns,
                "metrics": self.metrics,
                "sql": self.sql,
            },
            "edge_case": self.edge_case,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# 种子样本：33 条，按 §2.2 矩阵分布
# A(3) + B(8) + C(6) + D(4) + E(6) + F(3) + G(3) = 33
# ---------------------------------------------------------------------------
SEED_SAMPLES: list[Sample] = [
    # A. 单表无 JOIN（3 条）--------------------------------------------------
    Sample(
        id="A01",
        query="统计总订单数",
        category="single_table", joins=0, tables_count=1,
        tables=["fact_order"],
        columns=[],  # COUNT(*) 不依赖具体字段，只识别对 fact_order 表即可
        metrics=[],
        sql="SELECT COUNT(*) AS total_orders FROM fact_order",
        notes="COUNT(*)：只需识别 fact_order 表，无需具体字段",
    ),
    Sample(
        id="A02",
        query="所有订单的总金额",
        category="single_table", joins=0, tables_count=1,
        tables=["fact_order"],
        columns=["fact_order.order_amount"],
        metrics=[],
        sql="SELECT SUM(order_amount) AS total_amount FROM fact_order",
    ),
    Sample(
        id="A03",
        query="订单的平均金额",
        category="single_table", joins=0, tables_count=1,
        tables=["fact_order"],
        columns=["fact_order.order_amount"],
        metrics=[],
        sql="SELECT AVG(order_amount) AS avg_amount FROM fact_order",
    ),

    # B. 单维度分组（8 条，均 1 JOIN / 2 表）-------------------------------
    Sample(
        id="B01",
        query="各地区的销售总额",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_region"],
        columns=[
            "fact_order.region_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.region_name",
        ],
        metrics=[],
        sql=(
            "SELECT r.region_name, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
            "GROUP BY r.region_name"
        ),
    ),
    Sample(
        id="B02",
        query="各省份的订单数",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_region"],
        columns=[
            "fact_order.region_id", "dim_region.region_id", "dim_region.province",
        ],
        metrics=[],
        sql=(
            "SELECT r.province, COUNT(*) AS order_count "
            "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
            "GROUP BY r.province"
        ),
    ),
    Sample(
        id="B03",
        query="各会员等级的消费总额",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_customer"],
        columns=[
            "fact_order.customer_id", "fact_order.order_amount",
            "dim_customer.customer_id", "dim_customer.member_level",
        ],
        metrics=[],
        sql=(
            "SELECT c.member_level, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_customer c ON o.customer_id = c.customer_id "
            "GROUP BY c.member_level"
        ),
    ),
    Sample(
        id="B04",
        query="各性别的平均订单金额",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_customer"],
        columns=[
            "fact_order.customer_id", "fact_order.order_amount",
            "dim_customer.customer_id", "dim_customer.gender",
        ],
        metrics=[],
        sql=(
            "SELECT c.gender, AVG(o.order_amount) AS avg_amount "
            "FROM fact_order o JOIN dim_customer c ON o.customer_id = c.customer_id "
            "GROUP BY c.gender"
        ),
    ),
    Sample(
        id="B05",
        query="各品类的销售总额",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "fact_order.order_amount",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=[],
        sql=(
            "SELECT p.category, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY p.category"
        ),
    ),
    Sample(
        id="B06",
        query="各品牌的订单数量",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "dim_product.product_id", "dim_product.brand",
        ],
        metrics=[],
        sql=(
            "SELECT p.brand, COUNT(*) AS order_count "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY p.brand"
        ),
    ),
    Sample(
        id="B07",
        query="各月份的订单数",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_date"],
        columns=["fact_order.date_id", "dim_date.date_id", "dim_date.month"],
        metrics=[],
        sql=(
            "SELECT d.month, COUNT(*) AS order_count "
            "FROM fact_order o JOIN dim_date d ON o.date_id = d.date_id "
            "GROUP BY d.month"
        ),
    ),
    Sample(
        id="B08",
        query="各季度的销售总额",
        category="single_dim_group", joins=1, tables_count=2,
        tables=["fact_order", "dim_date"],
        columns=[
            "fact_order.date_id", "fact_order.order_amount",
            "dim_date.date_id", "dim_date.quarter",
        ],
        metrics=[],
        sql=(
            "SELECT d.quarter, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_date d ON o.date_id = d.date_id "
            "GROUP BY d.quarter"
        ),
    ),

    # C. 多维度交叉（6 条，2 JOIN / 3 表）----------------------------------
    Sample(
        id="C01",
        query="各会员等级在各地区的消费总额",
        category="multi_dim", joins=2, tables_count=3,
        tables=["fact_order", "dim_region", "dim_customer"],
        columns=[
            "fact_order.region_id", "fact_order.customer_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.region_name",
            "dim_customer.customer_id", "dim_customer.member_level",
        ],
        metrics=[],
        sql=(
            "SELECT c.member_level, r.region_name, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o "
            "JOIN dim_region r ON o.region_id = r.region_id "
            "JOIN dim_customer c ON o.customer_id = c.customer_id "
            "GROUP BY c.member_level, r.region_name"
        ),
    ),
    Sample(
        id="C02",
        query="各品类在各地区的销售总额",
        category="multi_dim", joins=2, tables_count=3,
        tables=["fact_order", "dim_region", "dim_product"],
        columns=[
            "fact_order.region_id", "fact_order.product_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.region_name",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=[],
        sql=(
            "SELECT p.category, r.region_name, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o "
            "JOIN dim_region r ON o.region_id = r.region_id "
            "JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY p.category, r.region_name"
        ),
    ),
    Sample(
        id="C03",
        query="各会员等级各品类的订单数",
        category="multi_dim", joins=2, tables_count=3,
        tables=["fact_order", "dim_customer", "dim_product"],
        columns=[
            "fact_order.customer_id", "fact_order.product_id",
            "dim_customer.customer_id", "dim_customer.member_level",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=[],
        sql=(
            "SELECT c.member_level, p.category, COUNT(*) AS order_count "
            "FROM fact_order o "
            "JOIN dim_customer c ON o.customer_id = c.customer_id "
            "JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY c.member_level, p.category"
        ),
    ),
    Sample(
        id="C04",
        query="各地区各月份的销售总额",
        category="multi_dim", joins=2, tables_count=3,
        tables=["fact_order", "dim_region", "dim_date"],
        columns=[
            "fact_order.region_id", "fact_order.date_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.region_name",
            "dim_date.date_id", "dim_date.month",
        ],
        metrics=[],
        sql=(
            "SELECT r.region_name, d.month, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o "
            "JOIN dim_region r ON o.region_id = r.region_id "
            "JOIN dim_date d ON o.date_id = d.date_id "
            "GROUP BY r.region_name, d.month"
        ),
    ),
    Sample(
        id="C05",
        query="各品牌在各会员等级的订单数",
        category="multi_dim", joins=2, tables_count=3,
        tables=["fact_order", "dim_product", "dim_customer"],
        columns=[
            "fact_order.product_id", "fact_order.customer_id",
            "dim_product.product_id", "dim_product.brand",
            "dim_customer.customer_id", "dim_customer.member_level",
        ],
        metrics=[],
        sql=(
            "SELECT p.brand, c.member_level, COUNT(*) AS order_count "
            "FROM fact_order o "
            "JOIN dim_product p ON o.product_id = p.product_id "
            "JOIN dim_customer c ON o.customer_id = c.customer_id "
            "GROUP BY p.brand, c.member_level"
        ),
    ),
    Sample(
        id="C06",
        query="各省份各品类的消费总额",
        category="multi_dim", joins=2, tables_count=3,
        tables=["fact_order", "dim_region", "dim_product"],
        columns=[
            "fact_order.region_id", "fact_order.product_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.province",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=[],
        sql=(
            "SELECT r.province, p.category, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o "
            "JOIN dim_region r ON o.region_id = r.region_id "
            "JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY r.province, p.category"
        ),
    ),

    # D. 指标查询（4 条，GMV / AOV）----------------------------------------
    Sample(
        id="D01",
        query="第一季度的 GMV 是多少",
        category="metric", joins=1, tables_count=2,
        tables=["fact_order", "dim_date"],
        columns=[
            "fact_order.date_id", "fact_order.order_amount",
            "dim_date.date_id", "dim_date.year", "dim_date.quarter",
        ],
        metrics=["GMV"],
        sql=(
            "SELECT SUM(o.order_amount) AS gmv "
            "FROM fact_order o JOIN dim_date d ON o.date_id = d.date_id "
            "WHERE d.year = 2025 AND d.quarter = 'Q1'"
        ),
        notes="GMV 语义 = SUM(order_amount)",
    ),
    Sample(
        id="D02",
        query="各地区的 GMV",
        category="metric", joins=1, tables_count=2,
        tables=["fact_order", "dim_region"],
        columns=[
            "fact_order.region_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.region_name",
        ],
        metrics=["GMV"],
        sql=(
            "SELECT r.region_name, SUM(o.order_amount) AS gmv "
            "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
            "GROUP BY r.region_name"
        ),
    ),
    Sample(
        id="D03",
        query="第一季度的 AOV 是多少",
        category="metric", joins=1, tables_count=2,
        tables=["fact_order", "dim_date"],
        columns=[
            "fact_order.date_id", "fact_order.order_amount",
            "dim_date.date_id", "dim_date.year", "dim_date.quarter",
        ],
        metrics=["AOV"],
        sql=(
            "SELECT AVG(o.order_amount) AS aov "
            "FROM fact_order o JOIN dim_date d ON o.date_id = d.date_id "
            "WHERE d.year = 2025 AND d.quarter = 'Q1'"
        ),
        notes="AOV 真实语义 = AVG(order_amount)；meta_config.yaml:175 误写为 order_quantity，本条按真实语义标注",
    ),
    Sample(
        id="D04",
        query="各品类的 AOV",
        category="metric", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "fact_order.order_amount",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=["AOV"],
        sql=(
            "SELECT p.category, AVG(o.order_amount) AS aov "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY p.category"
        ),
        notes="AOV 真实语义 = AVG(order_amount)",
    ),

    # E. 过滤条件（6 条）---------------------------------------------------
    Sample(
        id="E01",
        query="华南地区的销售额",
        category="filter", joins=1, tables_count=2,
        tables=["fact_order", "dim_region"],
        columns=[
            "fact_order.region_id", "fact_order.order_amount",
            "dim_region.region_id", "dim_region.region_name",
        ],
        metrics=[],
        sql=(
            "SELECT SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
            "WHERE r.region_name = '华南'"
        ),
    ),
    Sample(
        id="E02",
        query="2025年1月的订单数",
        category="filter", joins=1, tables_count=2,
        tables=["fact_order", "dim_date"],
        columns=["fact_order.date_id", "dim_date.date_id", "dim_date.year", "dim_date.month"],
        metrics=[],
        sql=(
            "SELECT COUNT(*) AS order_count "
            "FROM fact_order o JOIN dim_date d ON o.date_id = d.date_id "
            "WHERE d.year = 2025 AND d.month = 1"
        ),
    ),
    Sample(
        id="E03",
        query="黄金会员的订单数",
        category="filter", joins=1, tables_count=2,
        tables=["fact_order", "dim_customer"],
        columns=[
            "fact_order.customer_id", "dim_customer.customer_id", "dim_customer.member_level",
        ],
        metrics=[],
        sql=(
            "SELECT COUNT(*) AS order_count "
            "FROM fact_order o JOIN dim_customer c ON o.customer_id = c.customer_id "
            "WHERE c.member_level = '黄金'"
        ),
    ),
    Sample(
        id="E04",
        query="手机数码品类的销售总额",
        category="filter", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "fact_order.order_amount",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=[],
        sql=(
            "SELECT SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "WHERE p.category = '手机数码'"
        ),
    ),
    Sample(
        id="E05",
        query="苹果品牌的销售额",
        category="filter", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "fact_order.order_amount",
            "dim_product.product_id", "dim_product.brand",
        ],
        metrics=[],
        sql=(
            "SELECT SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "WHERE p.brand = '苹果'"
        ),
    ),
    Sample(
        id="E06",
        query="订单金额大于5000的订单数",
        category="filter", joins=0, tables_count=1,
        tables=["fact_order"],
        columns=["fact_order.order_amount"],
        metrics=[],
        sql="SELECT COUNT(*) AS order_count FROM fact_order WHERE order_amount > 5000",
    ),

    # F. 排序分页（3 条 TOP N）---------------------------------------------
    Sample(
        id="F01",
        query="销售额前3的品类",
        category="topn", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "fact_order.order_amount",
            "dim_product.product_id", "dim_product.category",
        ],
        metrics=[],
        sql=(
            "SELECT p.category, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY p.category ORDER BY total_amount DESC LIMIT 3"
        ),
    ),
    Sample(
        id="F02",
        query="订单数最多的前3个地区",
        category="topn", joins=1, tables_count=2,
        tables=["fact_order", "dim_region"],
        columns=["fact_order.region_id", "dim_region.region_id", "dim_region.region_name"],
        metrics=[],
        sql=(
            "SELECT r.region_name, COUNT(*) AS order_count "
            "FROM fact_order o JOIN dim_region r ON o.region_id = r.region_id "
            "GROUP BY r.region_name ORDER BY order_count DESC LIMIT 3"
        ),
    ),
    Sample(
        id="F03",
        query="销售额最高的前5个品牌",
        category="topn", joins=1, tables_count=2,
        tables=["fact_order", "dim_product"],
        columns=[
            "fact_order.product_id", "fact_order.order_amount",
            "dim_product.product_id", "dim_product.brand",
        ],
        metrics=[],
        sql=(
            "SELECT p.brand, SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_product p ON o.product_id = p.product_id "
            "GROUP BY p.brand ORDER BY total_amount DESC LIMIT 5"
        ),
    ),

    # G. 边界 case（3 条，均空结果）----------------------------------------
    Sample(
        id="G01",
        query="2024年的销售额",
        category="edge_case", joins=1, tables_count=2,
        tables=["fact_order", "dim_date"],
        columns=[
            "fact_order.date_id", "fact_order.order_amount",
            "dim_date.date_id", "dim_date.year",
        ],
        metrics=[],
        sql=(
            "SELECT SUM(o.order_amount) AS total_amount "
            "FROM fact_order o JOIN dim_date d ON o.date_id = d.date_id "
            "WHERE d.year = 2024"
        ),
        edge_case=True,
        notes="数据边界：仅 2025 Q1，2024 返回空集（SUM 为 NULL）",
    ),
    Sample(
        id="G02",
        query="钻石会员的数量",
        category="edge_case", joins=1, tables_count=2,
        tables=["fact_order", "dim_customer"],
        columns=[
            "fact_order.customer_id", "dim_customer.customer_id", "dim_customer.member_level",
        ],
        metrics=[],
        sql=(
            "SELECT COUNT(*) AS total "
            "FROM fact_order o JOIN dim_customer c ON o.customer_id = c.customer_id "
            "WHERE c.member_level = '钻石'"
        ),
        edge_case=True,
        notes="数据边界：会员等级仅 4 档（青铜/白银/黄金/铂金），无钻石，返回 0",
    ),
    Sample(
        id="G03",
        query="销售额为负数的订单数",
        category="edge_case", joins=0, tables_count=1,
        tables=["fact_order"],
        columns=["fact_order.order_amount"],
        metrics=[],
        sql="SELECT COUNT(*) AS total FROM fact_order WHERE order_amount < 0",
        edge_case=True,
        notes="数据边界：order_amount 均 > 0，返回 0",
    ),
]


async def self_check_sql(dw_repository, sql: str) -> tuple[bool, str | None]:
    """在 dw 库执行 SQL，返回 (是否通过, 错误信息)。

    使用现有 DwMysqlRepository.execute_sql，不引入新依赖。
    """
    try:
        await dw_repository.execute_sql(sql)
        return True, None
    except Exception as e:
        return False, str(e)


def write_jsonl(samples: list[dict], output_path: Path) -> None:
    """写入 JSONL（每行一个样本，ensure_ascii=False 保留中文）。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    logger.info(f"数据集已写入：{output_path}（共 {len(samples)} 条）")
