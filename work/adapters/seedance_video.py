from .base import GenerationAdapter


class SeedanceVideoAdapter(GenerationAdapter):
    provider = "seedance"
    adapter_name = "seedance_video"
    supported_task_types = {"video"}

    def build_request(self, task_type, model_config, payload):
        params = {**(model_config.get("params") or {}), **(payload.get("params") or {})}
        return {
            "model": model_config.get("model") or model_config.get("model_name") or "seedance-pro",
            "prompt": payload.get("prompt", ""),
            "negative_prompt": payload.get("negative_prompt", ""),
            "duration": payload.get("duration") or params.get("duration") or "6s",
            "aspect_ratio": payload.get("aspect_ratio") or params.get("aspect_ratio") or "16:9",
            "generation_mode": payload.get("generation_mode") or params.get("generation_mode") or "image_to_video",
            "reference_images": payload.get("reference_images") or payload.get("references") or [],
            "audio_reference": payload.get("audio_reference", ""),
        }
