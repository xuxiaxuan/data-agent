from langchain.chat_models import init_chat_model
from langchain_ollama import ChatOllama

from app.conf.app_config import app_config

# llm = init_chat_model(
#     model_name=app_config.llm.model_name,
#     model_provider="ollama",
#     api_key=app_config.llm.api_key,
#     base_url=app_config.llm.base_url,
#     temperature=0
# )
llm = ChatOllama(
    model=app_config.llm.model_name,
    base_url=app_config.llm.base_url,
    temperature=0
)

if __name__ == '__main__':
    for chunk in llm.stream("你好"):
        print(chunk.text)