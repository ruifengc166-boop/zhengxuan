from .base import GenerationAdapter
from .openai_image import OpenAIImageAdapter
from .kling_video import KlingVideoAdapter
from .seedance_video import SeedanceVideoAdapter


PROVIDER_ADAPTERS = {
    "openai": OpenAIImageAdapter(),
    "kling": KlingVideoAdapter(),
    "seedance": SeedanceVideoAdapter(),
}


def get_generation_adapter(provider):
    key = (provider or "").lower()
    return PROVIDER_ADAPTERS.get(key, GenerationAdapter())


def adapter_capabilities():
    items = []
    for provider, adapter in sorted(PROVIDER_ADAPTERS.items()):
        items.append({
            "provider": provider,
            "adapter_name": adapter.adapter_name,
            "supported_task_types": sorted(adapter.supported_task_types),
        })
    return items
