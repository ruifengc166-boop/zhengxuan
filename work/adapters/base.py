from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass
class AdapterPlan:
    provider: str
    model_name: str
    adapter_name: str
    task_type: str
    ready: bool
    message: str
    request: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model_name": self.model_name,
            "adapter_name": self.adapter_name,
            "task_type": self.task_type,
            "ready": self.ready,
            "message": self.message,
            "request": self.request,
        }


class GenerationAdapter:
    """Base class for provider-specific image/video generation adapters.

    The first production step is to normalize each provider request before real
    network calls are enabled. This keeps the workflow auditable and prevents
    accidental spending during prototype testing.
    """

    provider = "custom"
    adapter_name = "base"
    supported_task_types = set()

    def supports(self, task_type: str) -> bool:
        return task_type in self.supported_task_types

    def build_request(self, task_type: str, model_config: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_type": task_type,
            "provider": model_config.get("provider", self.provider),
            "model": model_config.get("model") or model_config.get("model_name", ""),
            "prompt": payload.get("prompt", ""),
            "references": payload.get("references") or payload.get("reference_images") or [],
            "params": {**(model_config.get("params") or {}), **(payload.get("params") or {})},
        }

    def plan(self, task_type: str, model_config: Dict[str, Any], payload: Dict[str, Any]) -> AdapterPlan:
        if not self.supports(task_type):
            return AdapterPlan(
                provider=model_config.get("provider", self.provider),
                model_name=model_config.get("model") or model_config.get("model_name", ""),
                adapter_name=self.adapter_name,
                task_type=task_type,
                ready=False,
                message=f"{self.adapter_name} 暂不支持 {task_type} 任务。",
                request={},
            )
        request = self.build_request(task_type, model_config, payload)
        return AdapterPlan(
            provider=model_config.get("provider", self.provider),
            model_name=model_config.get("model") or model_config.get("model_name", ""),
            adapter_name=self.adapter_name,
            task_type=task_type,
            ready=bool(model_config.get("api_key")),
            message="已生成供应商提交计划；真实提交请由后台 worker 执行。" if model_config.get("api_key") else "缺少 API Key，保持模拟模式。",
            request=request,
        )

    def submit(self, task_type: str, model_config: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """Network submission is intentionally not enabled in the base adapter."""
        return False, {"error": "adapter_submit_not_implemented", "plan": self.plan(task_type, model_config, payload).to_dict()}
