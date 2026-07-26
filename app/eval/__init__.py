"""RAGAS 评测模块（零侵入 Text-to-SQL Agent）

设计原则：
- 业务代码（app/agent app/services app/repositories app/clients app/api main.py）零改动
- 仅通过 graph.astream 的多 stream_mode 捕获中间产物
- 评测 LLM 使用 GLM-5.2 via 智谱 Anthropic 兼容端点，配置走 .env
"""
