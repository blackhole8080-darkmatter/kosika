"""
Kosika Integration Adapters

This module provides adapters for integrating with external AI services,
tool providers, and cloud platforms.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


@dataclass
class ModelRequest:
    """Standardized model request."""

    prompt: str
    max_tokens: int = 1000
    temperature: float = 0.7
    messages: Optional[List[Dict[str, str]]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    function_call: Optional[str] = None


@dataclass
class ModelResponse:
    """Standardized model response."""

    content: str
    model: str
    tokens_used: int
    finish_reason: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    cost: Optional[float] = None


class ModelProviderAdapter(ABC):
    """Abstract base class for model provider adapters."""

    @abstractmethod
    def execute(self, request: ModelRequest) -> ModelResponse:
        """Execute a model request."""
        pass

    @abstractmethod
    def get_available_models(self) -> List[str]:
        """Get list of available models."""
        pass

    @abstractmethod
    def estimate_cost(self, request: ModelRequest) -> float:
        """Estimate cost for a request."""
        pass


class OpenAIAdapter(ModelProviderAdapter):
    """Adapter for OpenAI API."""

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.client = None

        try:
            import openai

            self.client = openai.OpenAI(api_key=api_key, base_url=base_url)
        except ImportError:
            print("OpenAI library not installed. Install with: pip install openai")

    def execute(self, request: ModelRequest) -> ModelResponse:
        """Execute request using OpenAI API."""
        if not self.client:
            raise RuntimeError("OpenAI client not available")

        try:
            if request.messages:
                response = self.client.chat.completions.create(
                    model="gpt-4",
                    messages=request.messages,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    tools=request.tools,
                    function_call=request.function_call,
                )
            else:
                response = self.client.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": request.prompt}],
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                )

            choice = response.choices[0]
            tokens_used = response.usage.total_tokens

            return ModelResponse(
                content=choice.message.content or "",
                model=response.model,
                tokens_used=tokens_used,
                finish_reason=choice.finish_reason,
                tool_calls=choice.message.tool_calls,
                cost=self._calculate_cost(response.model, tokens_used),
            )

        except Exception as e:
            raise RuntimeError(f"OpenAI API error: {e}")

    def get_available_models(self) -> List[str]:
        """Get available OpenAI models."""
        return ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]

    def estimate_cost(self, request: ModelRequest) -> float:
        """Estimate cost for request."""
        # Rough estimation based on token count
        estimated_tokens = len(request.prompt) // 4 + request.max_tokens
        return (estimated_tokens / 1000) * 0.03  # GPT-4 pricing

    def _calculate_cost(self, model: str, tokens: int) -> float:
        """Calculate actual cost based on model and tokens."""
        pricing = {"gpt-4": 0.03, "gpt-4-turbo": 0.01, "gpt-3.5-turbo": 0.002}
        return (tokens / 1000) * pricing.get(model, 0.03)


class AnthropicAdapter(ModelProviderAdapter):
    """Adapter for Anthropic Claude API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = None

        try:
            import anthropic

            self.client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            print(
                "Anthropic library not installed. Install with: pip install anthropic"
            )

    def execute(self, request: ModelRequest) -> ModelResponse:
        """Execute request using Anthropic API."""
        if not self.client:
            raise RuntimeError("Anthropic client not available")

        try:
            if request.messages:
                # Convert OpenAI format to Anthropic format
                system_message = ""
                user_messages = []

                for msg in request.messages:
                    if msg["role"] == "system":
                        system_message = msg["content"]
                    else:
                        user_messages.append(msg)

                response = self.client.messages.create(
                    model="claude-3-opus-20240229",
                    max_tokens=request.max_tokens,
                    system=system_message,
                    messages=user_messages,
                )
            else:
                response = self.client.messages.create(
                    model="claude-3-opus-20240229",
                    max_tokens=request.max_tokens,
                    messages=[{"role": "user", "content": request.prompt}],
                )

            content = response.content[0].text
            tokens_used = response.usage.input_tokens + response.usage.output_tokens

            return ModelResponse(
                content=content,
                model=response.model,
                tokens_used=tokens_used,
                finish_reason=response.stop_reason,
                cost=self._calculate_cost(response.model, tokens_used),
            )

        except Exception as e:
            raise RuntimeError(f"Anthropic API error: {e}")

    def get_available_models(self) -> List[str]:
        """Get available Anthropic models."""
        return ["claude-3-opus-20240229", "claude-3-sonnet-20240229"]

    def estimate_cost(self, request: ModelRequest) -> float:
        """Estimate cost for request."""
        estimated_tokens = len(request.prompt) // 4 + request.max_tokens
        return (estimated_tokens / 1000) * 0.015  # Claude pricing

    def _calculate_cost(self, model: str, tokens: int) -> float:
        """Calculate actual cost based on model and tokens."""
        pricing = {"claude-3-opus-20240229": 0.015, "claude-3-sonnet-20240229": 0.003}
        return (tokens / 1000) * pricing.get(model, 0.015)


class LocalModelAdapter(ModelProviderAdapter):
    """Adapter for local models (e.g., Ollama, LocalAI)."""

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url

    def execute(self, request: ModelRequest) -> ModelResponse:
        """Execute request using local model API."""
        try:
            url = f"{self.base_url}/api/generate"
            payload = {
                "model": "llama2",
                "prompt": request.prompt,
                "stream": False,
                "options": {
                    "num_predict": request.max_tokens,
                    "temperature": request.temperature,
                },
            }

            response = requests.post(url, json=payload, timeout=60)
            response.raise_for_status()

            data = response.json()

            return ModelResponse(
                content=data.get("response", ""),
                model=data.get("model", "local"),
                tokens_used=data.get("eval_count", 0)
                + data.get("prompt_eval_count", 0),
                finish_reason=data.get("done_reason", "stop"),
                cost=0.0,  # Local models are free
            )

        except Exception as e:
            raise RuntimeError(f"Local model API error: {e}")

    def get_available_models(self) -> List[str]:
        """Get available local models."""
        try:
            url = f"{self.base_url}/api/tags"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
        except Exception:
            return ["llama2", "mistral", "codellama"]  # Default common models

    def estimate_cost(self, request: ModelRequest) -> float:
        """Estimate cost for request (always 0 for local models)."""
        return 0.0


class ToolProviderAdapter(ABC):
    """Abstract base class for tool provider adapters."""

    @abstractmethod
    def execute(self, operation: str, arguments: Dict[str, Any]) -> Any:
        """Execute a tool operation."""
        pass

    @abstractmethod
    def get_available_operations(self) -> List[str]:
        """Get list of available operations."""
        pass


class GitHubAdapter(ToolProviderAdapter):
    """Adapter for GitHub API tools."""

    def __init__(self, token: Optional[str] = None):
        self.token = token
        self.base_url = "https://api.github.com"

    def execute(self, operation: str, arguments: Dict[str, Any]) -> Any:
        """Execute GitHub API operation."""
        headers = {}
        if self.token:
            headers["Authorization"] = f"token {self.token}"

        try:
            if operation == "get_repo":
                url = f"{self.base_url}/repos/{arguments['owner']}/{arguments['repo']}"
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                return response.json()

            elif operation == "create_issue":
                url = f"{self.base_url}/repos/{arguments['owner']}/{arguments['repo']}/issues"
                response = requests.post(
                    url,
                    headers=headers,
                    json={
                        "title": arguments["title"],
                        "body": arguments.get("body", ""),
                    },
                )
                response.raise_for_status()
                return response.json()

            elif operation == "search_code":
                url = f"{self.base_url}/search/code"
                params = {
                    "q": f"repo:{arguments['owner']}/{arguments['repo']} {arguments['query']}"
                }
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

            else:
                raise ValueError(f"Unknown operation: {operation}")

        except Exception as e:
            raise RuntimeError(f"GitHub API error: {e}")

    def get_available_operations(self) -> List[str]:
        """Get available GitHub operations."""
        return ["get_repo", "create_issue", "search_code"]


class FileSystemAdapter(ToolProviderAdapter):
    """Adapter for filesystem operations (sandboxed)."""

    def __init__(self, allowed_paths: List[str]):
        self.allowed_paths = allowed_paths

    def execute(self, operation: str, arguments: Dict[str, Any]) -> Any:
        """Execute filesystem operation (with safety checks)."""
        from pathlib import Path

        file_path = Path(arguments["path"])

        # Security check: ensure path is within allowed directories
        if not any(
            str(file_path).startswith(allowed) for allowed in self.allowed_paths
        ):
            raise PermissionError(f"Path not in allowed directories: {file_path}")

        try:
            if operation == "read":
                return file_path.read_text()

            elif operation == "write":
                file_path.write_text(arguments["content"])
                return {"status": "success", "path": str(file_path)}

            elif operation == "list":
                if file_path.is_dir():
                    return [item.name for item in file_path.iterdir()]
                return []

            elif operation == "exists":
                return file_path.exists()

            else:
                raise ValueError(f"Unknown operation: {operation}")

        except Exception as e:
            raise RuntimeError(f"Filesystem error: {e}")

    def get_available_operations(self) -> List[str]:
        """Get available filesystem operations."""
        return ["read", "write", "list", "exists"]


class WebSearchAdapter(ToolProviderAdapter):
    """Adapter for web search operations."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def execute(self, operation: str, arguments: Dict[str, Any]) -> Any:
        """Execute web search operation."""
        if operation == "search":
            query = arguments["query"]

            # Placeholder: In production, use real search API
            # This is a mock implementation
            return {
                "results": [
                    {
                        "title": f"Mock result for: {query}",
                        "url": "https://example.com",
                        "snippet": f"This is a mock search result for the query: {query}",
                    }
                ],
                "total_results": 1,
            }

        else:
            raise ValueError(f"Unknown operation: {operation}")

    def get_available_operations(self) -> List[str]:
        """Get available web search operations."""
        return ["search"]


class IntegrationManager:
    """Manages multiple integration adapters."""

    def __init__(self):
        self.model_providers: Dict[str, ModelProviderAdapter] = {}
        self.tool_providers: Dict[str, ToolProviderAdapter] = {}

    def register_model_provider(self, name: str, adapter: ModelProviderAdapter):
        """Register a model provider adapter."""
        self.model_providers[name] = adapter

    def register_tool_provider(self, name: str, adapter: ToolProviderAdapter):
        """Register a tool provider adapter."""
        self.tool_providers[name] = adapter

    def get_model_provider(self, name: str) -> Optional[ModelProviderAdapter]:
        """Get a model provider by name."""
        return self.model_providers.get(name)

    def get_tool_provider(self, name: str) -> Optional[ToolProviderAdapter]:
        """Get a tool provider by name."""
        return self.tool_providers.get(name)

    def list_model_providers(self) -> List[str]:
        """List available model providers."""
        return list(self.model_providers.keys())

    def list_tool_providers(self) -> List[str]:
        """List available tool providers."""
        return list(self.tool_providers.keys())


# Convenience function to create a standard integration setup
def create_standard_integration(
    openai_key: Optional[str] = None,
    anthropic_key: Optional[str] = None,
    local_model_url: str = "http://localhost:11434",
    github_token: Optional[str] = None,
    allowed_paths: Optional[List[str]] = None,
) -> IntegrationManager:
    """Create a standard integration setup with common adapters."""

    manager = IntegrationManager()

    # Register model providers
    if openai_key:
        manager.register_model_provider("openai", OpenAIAdapter(openai_key))

    if anthropic_key:
        manager.register_model_provider("anthropic", AnthropicAdapter(anthropic_key))

    manager.register_model_provider("local", LocalModelAdapter(local_model_url))

    # Register tool providers
    if github_token:
        manager.register_tool_provider("github", GitHubAdapter(github_token))

    if allowed_paths:
        manager.register_tool_provider("filesystem", FileSystemAdapter(allowed_paths))

    manager.register_tool_provider("websearch", WebSearchAdapter())

    return manager
