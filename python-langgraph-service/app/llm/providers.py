"""
LLM Provider Abstraction Layer

This module provides a unified interface for multiple LLM providers using LangChain.
Supports: OpenAI, Anthropic, Ollama, Mistral, Groq, YandexGPT, and 50+ more via LangChain.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, AsyncIterator
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    async def generate(
        self,
        user_message: str,
        system_message: str = "You are a helpful assistant.",
        **kwargs
    ) -> str:
        """Generate a response from the LLM."""
        pass
    
    @abstractmethod
    async def stream(
        self,
        user_message: str,
        system_message: str = "You are a helpful assistant.",
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream response tokens from the LLM."""
        pass


class LangChainProvider(LLMProvider):
    """
    Universal LLM provider using LangChain's abstraction.
    Supports 50+ LLM providers through LangChain integrations.
    """
    
    def __init__(self, model: BaseChatModel):
        self.model = model
        logger.info(f"Initialized LangChain provider with model: {model.__class__.__name__}")
    
    async def generate(
        self,
        user_message: str,
        system_message: str = "You are a helpful assistant.",
        **kwargs
    ) -> str:
        """Generate a complete response."""
        messages = [
            SystemMessage(content=system_message),
            HumanMessage(content=user_message)
        ]
        
        response = await self.model.ainvoke(messages, **kwargs)
        return response.content
    
    async def stream(
        self,
        user_message: str,
        system_message: str = "You are a helpful assistant.",
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream response tokens."""
        messages = [
            SystemMessage(content=system_message),
            HumanMessage(content=user_message)
        ]
        
        async for chunk in self.model.astream(messages, **kwargs):
            if hasattr(chunk, 'content') and chunk.content:
                yield chunk.content


def create_llm_provider(
    provider_type: str,
    config: Dict[str, Any]
) -> LLMProvider:
    """
    Factory function to create LLM providers.
    
    Args:
        provider_type: One of 'openai', 'anthropic', 'ollama', 'mistral', 
                      'groq', 'yandex', etc.
        config: Provider-specific configuration
        
    Returns:
        Configured LLMProvider instance
        
    Examples:
        >>> # OpenAI
        >>> llm = create_llm_provider('openai', {
        ...     'api_key': 'sk-...',
        ...     'model': 'gpt-4'
        ... })
        
        >>> # Ollama (local)
        >>> llm = create_llm_provider('ollama', {
        ...     'base_url': 'http://localhost:11434',
        ...     'model': 'llama2'
        ... })
        
        >>> # Anthropic
        >>> llm = create_llm_provider('anthropic', {
        ...     'api_key': 'sk-ant-...',
        ...     'model': 'claude-3-opus-20240229'
        ... })
    """
    provider_type = provider_type.lower()
    
    if provider_type == 'openai':
        model = ChatOpenAI(
            api_key=config.get('api_key'),
            model=config.get('model', 'gpt-4'),
            temperature=config.get('temperature', 0.7),
            max_tokens=config.get('max_tokens'),
            base_url=config.get('base_url'),  # For custom endpoints
        )
        return LangChainProvider(model)
    
    elif provider_type == 'anthropic':
        model = ChatAnthropic(
            api_key=config.get('api_key'),
            model=config.get('model', 'claude-3-opus-20240229'),
            temperature=config.get('temperature', 0.7),
            max_tokens=config.get('max_tokens', 4096),
        )
        return LangChainProvider(model)
    
    elif provider_type == 'ollama':
        model = ChatOllama(
            base_url=config.get('base_url', 'http://localhost:11434'),
            model=config.get('model', 'llama2'),
            temperature=config.get('temperature', 0.7),
            num_predict=config.get('max_tokens'),
        )
        return LangChainProvider(model)
    
    elif provider_type == 'mistral':
        from langchain_mistralai import ChatMistralAI
        model = ChatMistralAI(
            api_key=config.get('api_key'),
            model=config.get('model', 'mistral-large-latest'),
            temperature=config.get('temperature', 0.7),
            max_tokens=config.get('max_tokens'),
        )
        return LangChainProvider(model)
    
    elif provider_type == 'groq':
        from langchain_groq import ChatGroq
        model = ChatGroq(
            api_key=config.get('api_key'),
            model=config.get('model', 'mixtral-8x7b-32768'),
            temperature=config.get('temperature', 0.7),
            max_tokens=config.get('max_tokens'),
        )
        return LangChainProvider(model)
    
    elif provider_type == 'yandex':
        # Keep existing Yandex client for backward compatibility
        from app.clients.yandex_client import YandexGPTClient
        return YandexProvider(
            api_key=config.get('api_key'),
            folder_id=config.get('folder_id'),
            model_name=config.get('model', 'yandexgpt'),
        )
    
    else:
        # Try to use generic LangChain integration
        try:
            # Dynamic import for less common providers
            module_name = f"langchain_{provider_type}"
            module = __import__(module_name, fromlist=[''])
            model_class = getattr(module, f'Chat{provider_type.capitalize()}')
            
            model = model_class(
                api_key=config.get('api_key'),
                model=config.get('model'),
                **{k: v for k, v in config.items() if k not in ['api_key', 'model']}
            )
            return LangChainProvider(model)
        except (ImportError, AttributeError) as e:
            raise ValueError(
                f"Unsupported provider: {provider_type}. "
                f"Install langchain-{provider_type} package or use a supported provider."
            ) from e


class YandexProvider(LLMProvider):
    """Wrapper for YandexGPT to match LLMProvider interface."""
    
    def __init__(self, api_key: str, folder_id: str, model_name: str = "yandexgpt"):
        self.client = YandexGPTClient(api_key=api_key, folder_id=folder_id)
        self.model_name = model_name
    
    async def generate(
        self,
        user_message: str,
        system_message: str = "Ты умный помощник.",
        **kwargs
    ) -> str:
        return await self.client.generate(
            user_message=user_message,
            system_message=system_message,
            model_name=self.model_name,
            temperature=kwargs.get('temperature', 0.6),
            max_tokens=kwargs.get('max_tokens', 2000)
        )
    
    async def stream(
        self,
        user_message: str,
        system_message: str = "Ты умный помощник.",
        **kwargs
    ) -> AsyncIterator[str]:
        # Yandex doesn't support streaming in current implementation
        # Return full response as single chunk
        response = await self.generate(user_message, system_message, **kwargs)
        yield response
