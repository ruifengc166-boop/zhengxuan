from typing import Any, Dict

from adapters.provider_registry import adapter_capabilities, get_generation_adapter


def build_generation_submission_plan(task_type: str, model_config: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a provider-normalized request without making billable network calls."""
    provider = model_config.get("provider", "")
    adapter = get_generation_adapter(provider)
    return adapter.plan(task_type, model_config, payload).to_dict()


def list_generation_capabilities():
    return adapter_capabilities()
