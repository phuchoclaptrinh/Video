from app.pipelines.planner_transport import generation_params


def test_reasoning_models_use_completion_token_budget_without_temperature(monkeypatch):
    monkeypatch.delenv("OPENAI_REASONING_EFFORT", raising=False)
    params = generation_params("gpt-6-luna", temperature=0.7, max_tokens=4500)
    assert params == {"max_completion_tokens": 12500, "reasoning_effort": "low"}


def test_classic_chat_models_keep_temperature_and_max_tokens():
    assert generation_params("gpt-4o-mini", temperature=0.7, max_tokens=4500) == {
        "temperature": 0.7, "max_tokens": 4500,
    }
