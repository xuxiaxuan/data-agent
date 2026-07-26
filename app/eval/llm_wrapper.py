"""评测 LLM / Embeddings 封装

- LLM：GLM-5.2 via 智谱 Anthropic 兼容端点，用 langchain-anthropic 的 ChatAnthropic 直接对接
- Embeddings：复用项目现有 LocalOpenAIEmbedding（OpenAI 兼容 /v1/embeddings 协议），
  通过 BaseEmbeddings 适配器包装，供 RAGAS 使用

设计原则：
- 单例缓存，避免每条样本重建客户端
- 凭证从 .env 读取，绝不硬编码
"""
from __future__ import annotations

import os
from typing import List

from langchain_anthropic import ChatAnthropic
from langchain_core.embeddings import Embeddings

from app.clients.embedding_client_manager import LocalOpenAIEmbedding, embedding_client_manager
from app.conf.app_config import app_config
from app.core.log import logger

# 单例缓存
_eval_llm: ChatAnthropic | None = None
_eval_llm_wrapper: "GLM5LangchainLLMWrapper | None" = None
_eval_embeddings: "EvalEmbeddingsAdapter | None" = None


class EvalEmbeddingsAdapter(Embeddings):
    """将项目现有 LocalOpenAIEmbedding 适配为 langchain BaseEmbeddings。

    LocalOpenAIEmbedding 已实现 aembed_query / aembed_documents，
    这里补齐同步接口，RAGAS 默认走异步路径。
    """

    def __init__(self, client: LocalOpenAIEmbedding):
        self._client = client

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        # langchain Embeddings 同步接口；RAGAS 0.2.x 默认走 aembed_documents
        import asyncio

        return asyncio.get_event_loop().run_until_complete(self._client.aembed_documents(texts))

    def embed_query(self, text: str) -> List[float]:
        import asyncio

        return asyncio.get_event_loop().run_until_complete(self._client.aembed_query(text))

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self._client.aembed_documents(texts)

    async def aembed_query(self, text: str) -> List[float]:
        return await self._client.aembed_query(text)


class GLM5LangchainLLMWrapper:
    """RAGAS LangchainLLMWrapper 的 GLM-5.2 兼容版。

    问题：RAGAS 0.2.x 默认 temperature=1e-8（即 0.00000001），调用 agenerate_text 时会
    通过 `self.langchain_llm.temperature = temperature` 把这个值塞给底层 LLM。
    智谱 Anthropic 兼容端点要求 temperature ≤ 2 位小数，会返回 400 错误：
        [1210][temperature参数非法,必须小于等于[2]位]

    解决：动态继承 LangchainLLMWrapper 并重写 get_temperature 强制返回 0.0。
    RAGAS 内部 agenerate_text 中：
        if temperature is None:
            temperature = self.get_temperature(n=n)   # ← 我们在这里返回 0.0
        self.langchain_llm.temperature = temperature  # ← 因此被设成 0 而非 1e-8

    使用：build_eval_llm_wrapper() 代替 LangchainLLMWrapper(llm)
    """

    def __new__(cls, langchain_llm: ChatAnthropic):
        # lazy import + 动态继承，避免 ragas 未装时模块顶层报错
        from ragas.llms import LangchainLLMWrapper

        class _Impl(LangchainLLMWrapper):
            def get_temperature(self, n: int) -> float:  # type: ignore[override]
                # 智谱端点 ≤2 位小数限制，强制返回 0.0
                return 0.0

        return _Impl(langchain_llm)


def build_eval_llm() -> ChatAnthropic:
    """构造评测用 ChatAnthropic（GLM-5.2）。单例。"""
    global _eval_llm
    if _eval_llm is not None:
        return _eval_llm

    api_key = os.environ.get("EVAL_LLM_API_KEY", "")
    base_url = os.environ.get("EVAL_LLM_BASE_URL", "https://open.bigmodel.cn/api/anthropic")
    model = os.environ.get("EVAL_LLM_MODEL", "glm-5.2")

    if not api_key:
        raise RuntimeError(
            "缺少 EVAL_LLM_API_KEY，请在 .env 中配置（智谱 Anthropic 兼容端点的 API Key）"
        )

    _eval_llm = ChatAnthropic(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        max_tokens=4096,
    )
    logger.info(f"评测 LLM 已初始化：model={model}, base_url={base_url}")
    return _eval_llm


def build_eval_llm_wrapper() -> "GLM5LangchainLLMWrapper":
    """构造 RAGAS LLM Wrapper（带 GLM-5.2 兼容修复）。单例。"""
    global _eval_llm_wrapper
    if _eval_llm_wrapper is None:
        _eval_llm_wrapper = GLM5LangchainLLMWrapper(build_eval_llm())
    return _eval_llm_wrapper


def build_eval_embeddings() -> EvalEmbeddingsAdapter:
    """构造评测用 Embeddings（复用项目 embedding 服务）。单例。

    前置条件：调用方需先 embedding_client_manager.init()
    """
    global _eval_embeddings
    if _eval_embeddings is not None:
        return _eval_embeddings

    if embedding_client_manager.client is None:
        embedding_client_manager.init()

    _eval_embeddings = EvalEmbeddingsAdapter(embedding_client_manager.client)
    logger.info(
        f"评测 Embeddings 已初始化：复用本地服务 "
        f"http://{app_config.embedding.host}:{app_config.embedding.port}"
    )
    return _eval_embeddings
