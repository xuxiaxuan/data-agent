"""RAGAS 兼容性 shim

问题背景：
- RAGAS 0.2.x 在 `ragas/llms/base.py` 顶层硬编码：
    `from langchain_community.chat_models.vertexai import ChatVertexAI`
- 但 `langchain-community >= 0.3` 已移除该模块（拆到独立的 `langchain-google-vertexai`）
- 导致 `import ragas` / `from ragas.metrics import ...` 抛 ModuleNotFoundError

解决方案（不修改第三方包）：
- 在 sys.modules 注入一个 stub 模块，让 RAGAS 的 import 满足
- 我们评测用 GLM-5.2（Anthropic 兼容端点），根本用不到 VertexAI，stub 个空类即可

调用约定：
- 在任何 `from ragas.*` 或 `import ragas` 之前调用 `ensure_ragas_compat()`
- 函数幂等，多次调用安全
"""
from __future__ import annotations

import sys
import types

_COMPAT_INSTALLED = False


def ensure_ragas_compat() -> None:
    """注入 langchain_community.chat_models.vertexai stub，绕过 RAGAS 顶层 import 失败。"""
    global _COMPAT_INSTALLED
    if _COMPAT_INSTALLED:
        return

    mod_name = "langchain_community.chat_models.vertexai"
    if mod_name not in sys.modules:
        try:
            __import__(mod_name)
        except ImportError:
            stub = types.ModuleType(mod_name)

            class ChatVertexAI:  # 最小 stub，仅满足类型 import
                """Stub for ChatVertexAI（评测不使用 VertexAI，仅满足 RAGAS import）"""

            stub.ChatVertexAI = ChatVertexAI
            sys.modules[mod_name] = stub

    _COMPAT_INSTALLED = True
