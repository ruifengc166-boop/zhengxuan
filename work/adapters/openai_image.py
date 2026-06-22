from .base import GenerationAdapter


class OpenAIImageAdapter(GenerationAdapter):
    provider = "openai"
    adapter_name = "openai_image"
    supported_task_types = {"image"}

    def build_request(self, task_type, model_config, payload):
        params = {**(model_config.get("params") or {}), **(payload.get("params") or {})}
        return {
            "endpoint": "/images/generations",
            "model": model_config.get("model") or model_config.get("model_name") or "gpt-image-1",
            "prompt": payload.get("prompt", ""),
            "size": payload.get("size") or params.get("size") or "1024x1024",
            "n": int(payload.get("n") or params.get("n") or 1),
            "quality": payload.get("quality") or params.get("quality", "standard"),
            "references": payload.get("references") or payload.get("reference_images") or [],
        }
