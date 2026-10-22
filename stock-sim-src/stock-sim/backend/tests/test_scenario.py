"""시나리오 생성 (SPEC 6.4 FR-SC). 실제 LLM 호출 없음 — 가짜 LLM."""

from __future__ import annotations

import json

import pytest

from app.services.scenario_service import (
    KEYS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    LLMResult,
    generate_scenario,
    template_scenario,
    validate,
)

INPUT = {
    "종목명": "삼성전자",
    "기준일": "2026-10-21",
    "기준일_종가": "223,500원",
    "예측": {
        "5거래일_상승확률": "58%",
        "KOSPI_초과확률": "53%",
        "예상_수익률_범위": {"하단": "-2.1%", "중앙": "+0.9%", "상단": "+4.3%"},
        "예상_가격_범위": {"하단": "219,000원", "중앙": "225,500원", "상단": "233,000원"},
    },
    "근거_TOP5": ["최근 5일 외국인 순매수 (시총 대비 +0.12%)", "최근 20거래일 +8.3% 상승"],
    "관련_뉴스": [{"제목": "반도체 업황 개선 기대", "날짜": "2026-10-20"}],
    "시장_상황": {"KOSPI_5일_수익률": "+1.2%"},
}

GOOD = {
    "summary": "삼성전자의 5거래일 상승확률은 58%로 추정됩니다. 관련 요인으로 외국인 수급과 최근 상승 흐름이 함께 나타납니다.",
    "bull": "강세 흐름에서는 233,000원 부근까지 열려 있습니다. 과거 유사 사례에서는 수급 흐름이 함께 나타났습니다.",
    "base": "중앙 예측은 +0.9%로 225,500원 부근입니다. 범위 안의 등락 가능성을 함께 볼 필요가 있습니다.",
    "bear": "약세 흐름에서는 219,000원 부근까지 내려갈 수 있습니다. 예측에는 불확실성이 있습니다.",
}


class FakeLLM:
    model = "fake-model"

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), 0

    def generate(self, system, user):
        self.calls += 1
        assert "입력 JSON 에 없는 사실" in system and json.loads(user)["종목명"] == "삼성전자"
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return LLMResult(
            out if isinstance(out, str) else json.dumps(out, ensure_ascii=False), "fake-model", 100, 50
        )


def test_system_prompt_contains_spec_rules_FR_SC_3():
    for rule in (
        "입력 JSON 에 없는 사실",
        "새로 계산하거나",
        "인과를 단정하지 않는다",
        "매수, 매도, 보유를 권하거나",
        "입력값 그대로",
    ):
        assert rule in SYSTEM_PROMPT


def test_valid_llm_output_is_used_and_tokens_recorded_FR_SC_6():
    llm = FakeLLM(GOOD)
    r = generate_scenario(INPUT, llm)
    assert r.generated_by == "llm" and r.summary == GOOD["summary"] and llm.calls == 1
    assert (r.llm_model, r.prompt_version, r.input_tokens, r.output_tokens) == (
        "fake-model",
        PROMPT_VERSION,
        100,
        50,
    )


@pytest.mark.parametrize(
    "bad,problem",
    [
        (
            {**GOOD, "bull": "강세 흐름에서는 12.5% 오를 수 있습니다. 수급이 함께 나타납니다."},
            "입력에 없는 숫자",
        ),
        ({**GOOD, "bear": "환율 상승 때문에 하락할 수 있습니다. 불확실성이 있습니다."}, "인과 단정"),
        ({**GOOD, "base": "지금 매수하세요. 중앙 예측은 +0.9%입니다."}, "매매 권유"),
        ({**GOOD, "summary": "한 문장뿐입니다."}, "문장 수"),
        ({"summary": "x"}, "출력 형식"),
    ],
)
def test_validation_catches_each_violation_FR_SC_4(bad, problem):
    assert any(problem in e for e in validate(bad, INPUT))


def test_validation_accepts_sign_rephrasing_and_fact_words():
    ok = {**GOOD, "bear": "하단은 2.1% 하락한 219,000원 부근입니다. 외국인 순매수 흐름도 관련 요인입니다."}
    assert validate(ok, INPUT) == []  # -2.1% 를 '2.1% 하락'으로 쓰는 것과 '순매수' 사실 서술은 허용


def test_retry_once_then_succeed_sums_tokens():
    llm = FakeLLM({**GOOD, "bull": "99.9% 오릅니다. 확실합니다."}, GOOD)
    r = generate_scenario(INPUT, llm)
    assert r.generated_by == "llm" and llm.calls == 2 and r.input_tokens == 200 and r.output_tokens == 100


def test_two_failures_fall_back_to_template_FR_SC_4():
    bad = {**GOOD, "bull": "50% 급등이 예상됩니다. 지금 사세요."}
    llm = FakeLLM(bad, "not json")
    r = generate_scenario(INPUT, llm)
    assert r.generated_by == "template" and llm.calls == 2 and len(r.errors) == 2
    assert r.summary == template_scenario(INPUT)["summary"] and r.llm_model == "fake-model"


def test_llm_outage_falls_back_without_retry_FR_SC_5():
    llm = FakeLLM(RuntimeError("503"))
    r = generate_scenario(INPUT, llm)
    assert r.generated_by == "template" and llm.calls == 1 and "RuntimeError" in r.errors[0]


def test_no_llm_configured_uses_template():
    r = generate_scenario(INPUT, None)
    assert r.generated_by == "template" and r.llm_model is None


def test_template_itself_passes_validation():
    """대체 문장도 같은 규칙(입력 숫자만, 인과·권유 없음, 2~4문장)을 지킨다."""
    t = template_scenario(INPUT)
    assert set(t) == set(KEYS) and validate(t, INPUT) == []
    t2 = template_scenario({**INPUT, "근거_TOP5": []})  # 근거가 없어도 동작
    assert validate(t2, {**INPUT, "근거_TOP5": []}) == []


def test_anthropic_client_request_shape(monkeypatch):
    """실제 API 는 부르지 않고 요청 모양만 확인: 구조화 출력 + 서버 측 대체 + 거부 처리."""
    from types import SimpleNamespace

    from app.services import scenario_service as ss

    seen = {}

    def create(**kw):
        seen.update(kw)
        text = SimpleNamespace(type="text", text=json.dumps(GOOD, ensure_ascii=False))
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[text],
            model=kw["model"],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    llm = ss.AnthropicLLM("claude-opus-5-5", api_key="test-key")
    monkeypatch.setattr(llm.client.beta.messages, "create", create)
    r = generate_scenario(INPUT, llm)
    assert r.generated_by == "llm" and r.input_tokens == 10
    assert seen["output_config"]["format"]["type"] == "json_schema" and seen["fallbacks"] == "default"
    assert seen["betas"] == ["server-side-fallback-2026-07-01"] and seen["system"] == SYSTEM_PROMPT

    def refuse(**kw):
        return SimpleNamespace(
            stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"), content=[]
        )

    monkeypatch.setattr(llm.client.beta.messages, "create", refuse)
    r = generate_scenario(INPUT, llm)
    assert r.generated_by == "template" and "LLMRefused" in r.errors[0]
