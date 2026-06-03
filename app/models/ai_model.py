AI_MODEL_CONFIG_KEY = "__ai_model_config__"

AI_MODEL_PRESET_ORDER = ["kimi", "qwen", "deepseek", "openai", "custom"]

AI_MODEL_PRESETS = {
    "kimi": {
        "provider_type": "kimi",
        "provider_name": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model_options": ["kimi-k2.6"],
        "model": "kimi-k2.6",
        "api_key": "sk-e5OUafm3yFex3hWjsgS7rZw68hNtYJsJLji0u7ruEKqhQavU",
        "builtin": True,
    },
    "qwen": {
        "provider_type": "qwen",
        "provider_name": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model_options": ["qwen3.6-plus"],
        "model": "qwen3.6-plus",
        "api_key": "sk-f69eb070ffaa4174af6d85b408833aa4",
        "builtin": True,
    },
    "deepseek": {
        "provider_type": "deepseek",
        "provider_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model_options": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "model": "deepseek-v4-pro",
        "api_key": "sk-4cf758981a69465caecaf3c1b8ef3d66",
        "builtin": True,
    },
    "openai": {
        "provider_type": "openai",
        "provider_name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model_options": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"],
        "model": "gpt-4.1-mini",
        "api_key": "",
        "builtin": True,
    },
    "custom": {
        "provider_type": "custom",
        "provider_name": "自定义供应商",
        "base_url": "",
        "model_options": [],
        "model": "",
        "api_key": "",
        "builtin": False,
    },
}

AI_MODEL_DEFAULTS = AI_MODEL_PRESETS
