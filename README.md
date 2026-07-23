# DataAgent

基于 **LangGraph** 的 Text-to-SQL 智能查询助手 —— 通过自然语言查询数据库，自动完成 SQL 生成、校验、纠错与执行。

## 项目特性

- 自然语言到 SQL 的智能转换（Text-to-SQL）
- 基于 LangGraph 的多节点编排工作流
- 元数据知识库：字段、取值、指标的向量检索
- 中文分词（jieba）+ 语义检索（Qdrant + HuggingFace Embedding）
- SQL 自动校验与纠错闭环
- 全异步架构（FastAPI + SQLAlchemy + asyncmy）

## 技术栈

### 后端 (`/`)

| 分类 | 技术 |
|------|------|
| Web 框架 | FastAPI + uvicorn |
| Agent 编排 | LangChain + LangGraph |
| LLM | langchain-ollama（本地大模型） |
| Embedding | langchain-huggingface |
| 向量库 | Qdrant |
| 检索 | Elasticsearch |
| 数据库 | MySQL（asyncmy 异步驱动） |
| 配置 | omegaconf |
| 日志 | loguru |
| 依赖管理 | uv（Python ≥ 3.13） |

### 前端 (`data-agent-frontend/`)

| 分类 | 技术 |
|------|------|
| 框架 | Vue 3 |
| 构建工具 | Vite 7 |

## 目录结构

```
data-agent/
├── app/                        # 后端应用
│   ├── agent/                  #   LangGraph Agent 节点
│   ├── api/                    #   FastAPI 路由
│   ├── clients/                #   外部客户端封装
│   ├── core/                   #   核心（生命周期、上下文）
│   ├── entities/               #   数据实体
│   ├── models/                 #   数据模型
│   ├── prompt/                 #   提示词加载
│   ├── repositories/           #   数据访问层
│   ├── scripts/                #   脚本（元数据建库等）
│   └── services/               #   业务服务层
├── conf/                       # 配置文件
│   ├── app_config.yaml         #   应用主配置
│   └── meta_config.yaml        #   元数据知识库配置
├── prompts/                    # Prompt 模板（生成/校验/纠错 SQL 等）
├── data-agent-frontend/        # 前端项目（Vue 3 + Vite）
├── main.py                     # 后端入口（端口 8000）
├── pyproject.toml              # Python 依赖声明
└── uv.lock                     # 依赖锁文件
```

## 快速开始

### 环境要求

- Python ≥ 3.13
- Node.js ≥ 18
- MySQL 8+
- Qdrant（向量库）
- Elasticsearch 8+
- Ollama（本地大模型服务）

### 后端启动

```bash
# 安装依赖
uv sync

# 复制环境变量模板并填入真实密码（数据库等敏感信息）
cp .env.example .env
# 编辑 .env 填入 DB_META_PASSWORD / DB_DW_PASSWORD 等

# 按实际环境编辑配置
#   conf/app_config.yaml      数据库、模型、服务等
#   conf/meta_config.yaml     元数据知识库

# 启动服务（默认监听 0.0.0.0:8000）
python main.py
```

### 前端启动

```bash
cd data-agent-frontend
npm install
npm run dev
```

## 工作流程（Text-to-SQL Agent）

```
用户问题
   │
   ▼
[1] 关键词抽取
   │
   ▼
[2] 召回（字段 / 取值 / 指标）
   │
   ▼
[3] 过滤表与指标
   │
   ▼
[4] 生成 SQL
   │
   ▼
[5] 校验 SQL ──失败──► [6] 纠正 SQL ──► 回到 [5]
   │
  成功
   │
   ▼
[7] 执行 SQL，返回结果
```

## 配置说明

- `conf/app_config.yaml`：应用主配置（数据库连接、模型端点、服务端口等）
- `conf/meta_config.yaml`：元数据知识库配置（向量索引、表/字段/指标信息）
- `.env`：敏感信息（密钥等），已被 `.gitignore` 忽略，需自行创建

## 相关 Prompt 模板

| 文件 | 用途 |
|------|------|
| `generate_sql.prompt` | SQL 生成 |
| `correct_sql.prompt` | SQL 纠错 |
| `filter_table_info.prompt` | 表信息过滤 |
| `filter_metric_info.prompt` | 指标信息过滤 |
| `extend_keywords_for_column_recall.prompt` | 字段召回关键词扩展 |
| `extend_keywords_for_value_recall.prompt` | 取值召回关键词扩展 |
| `extend_keywords_for_metric_recall.prompt` | 指标召回关键词扩展 |
