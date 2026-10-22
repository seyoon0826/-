"""LLM(Claude)으로 강세/기본/약세 시나리오 생성 (SPEC 6.4 FR-SC). LLM 은 설명문만 쓰고 예측은 하지 않는다.

- 입력(FR-SC-1): 종목명, 기준일 종가, 예측값, 예상 가격 범위, 근거 TOP5 문장, 관련 뉴스, 시장 상황.
  숫자는 화면에 보일 문자열 그대로 넣는다("58%", "70,600원") — 모델이 새로 계산하지 않게.
- 출력(FR-SC-2): {"bull","base","bear","summary"} 각 2~4문장, 한국어. 구조화 출력(JSON 스키마)으로 받는다.
- 사후 검증(FR-SC-4): 출력의 숫자(%, 원)가 입력에 있는지, 인과 단정/매매 권유 표현이 없는지 검사.
  실패하면 1회 재시도, 또 실패하면 템플릿 문장(generated_by=template).
- LLM 장애/키 없음(FR-SC-5): 템플릿으로 대체하고 예측 저장은 계속한다.
- 기록(FR-SC-6): 모델명, 프롬프트 버전, 토큰 사용량.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

PROMPT_VERSION = "scenario-v1"
KEYS = ("summary", "bull", "base", "bear")
DISCLAIMER = "본 서비스는 교육·모의투자 목적이며 예측은 투자 권유가 아닙니다."

SYSTEM_PROMPT = """당신은 교육용 주식 모의투자 서비스에서 예측 모델의 결과를 설명하는 문장을 쓴다.
입력 JSON 은 이미 계산된 예측 결과다. 당신은 예측하지 않고, 입력을 읽기 쉬운 시나리오 문장으로 옮긴다.

반드시 지킬 규칙:
1. 입력 JSON 에 없는 사실, 수치, 사건, 기업 정보, 전망을 쓰지 않는다. 배경지식으로 내용을 보태지 않는다.
2. 퍼센트(%)와 원 단위 숫자는 입력에 적힌 값을 그대로만 쓴다. 새로 계산하거나 반올림하거나 바꾸지 않는다.
3. 인과를 단정하지 않는다. "원인", "때문에", "덕분에", "로 인해" 같은 표현 대신
   "관련 요인", "함께 나타난", "과거 유사 사례에서는" 같은 표현을 쓴다.
4. 매수, 매도, 보유를 권하거나 투자 판단을 지시하지 않는다.
5. 확률과 예상 범위는 입력값 그대로 전달한다.
6. summary(요약), bull(강세), base(기본), bear(약세)를 각각 2~4문장의 한국어로 쓴다.
   bull 은 예상 범위 상단 쪽, base 는 중앙값 쪽, bear 는 하단 쪽 흐름을 설명한다."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in KEYS},
    "required": list(KEYS),
    "additionalProperties": False,
}

# 인과 단정과 매매 권유 (FR-SC-3). "외국인 순매수" 같은 사실 서술은 걸리지 않게 권유 동사와 붙은 경우만 본다.
_CAUSAL = re.compile(r"원인|때문에|덕분에|(?:으)?로 인해|탓에|탓으로")
_ADVICE = re.compile(
    r"(?:매수|매도|보유|투자)(?:를|을)?\s*(?:권|추천|하세요|하십시오|해야|하는 것이 좋|할 것을)"
    r"|사세요|파세요|사야|팔아야"
)
_NUMBER = re.compile(r"([+-]?\d[\d,]*(?:\.\d+)?)\s*(%p|%|원)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=다\.)\s*")


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    model: str

    def generate(self, system: str, user: str) -> LLMResult: ...


class LLMRefused(RuntimeError):
    pass


@dataclass
class ScenarioResult:
    summary: str
    bull: str
    base: str
    bear: str
    generated_by: str  # llm | template
    llm_model: str | None = None
    prompt_version: str = PROMPT_VERSION
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)  # 검증 실패/장애 사유 (로그용)


# ---------------------------------------------------------------- Anthropic 클라이언트
class AnthropicLLM:
    """Claude Messages API. 구조화 출력으로 JSON 을 받고, 안전 분류기 거부 시 서버 측 대체 모델을 쓴다."""

    def __init__(self, model: str, api_key: str | None = None, effort: str = "medium") -> None:
        import anthropic

        self.model, self.effort = model, effort
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def generate(self, system: str, user: str) -> LLMResult:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4_000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # 안전 분류기 거부 시 Anthropic 권장 모델로 서버에서 재시도
        )
        if response.stop_reason == "refusal":
            raise LLMRefused(
                f"LLM 이 요청을 거절했습니다 ({getattr(response.stop_details, 'category', None)})"
            )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return LLMResult(text, response.model, response.usage.input_tokens, response.usage.output_tokens)


def default_llm(api_key: str, model: str) -> LLMClient | None:
    """키와 모델이 모두 있어야 LLM 을 쓴다. 없으면 None -> 템플릿 (FR-SC-5)."""
    if not api_key or not model:
        return None
    return AnthropicLLM(model, api_key)


# ---------------------------------------------------------------- 검증 (FR-SC-4)
def _numbers(text: str) -> set[tuple[float, str]]:
    out = set()
    for num, unit in _NUMBER.findall(text):
        unit = "%" if unit == "%p" else unit
        out.add(
            (abs(float(num.replace(",", ""))), unit)
        )  # 부호는 "하락/상승" 문장으로 바꿔 쓸 수 있어 절댓값 비교
    return out


def _sentences(text: str) -> int:
    return len([s for s in _SENTENCE_END.split(text.strip()) if s.strip()])


def validate(output: dict[str, Any], input_json: dict[str, Any]) -> list[str]:
    """문제 목록 (비어 있으면 통과)."""
    errs: list[str] = []
    if set(output) != set(KEYS) or not all(isinstance(output[k], str) and output[k].strip() for k in KEYS):
        return ["출력 형식이 {summary, bull, base, bear} 문자열이 아닙니다"]
    allowed = _numbers(json.dumps(input_json, ensure_ascii=False))
    for k in KEYS:
        text = output[k]
        extra = _numbers(text) - allowed
        if extra:
            errs.append(f"{k}: 입력에 없는 숫자 {sorted(extra)}")
        if _CAUSAL.search(text):
            errs.append(f"{k}: 인과 단정 표현")
        if _ADVICE.search(text):
            errs.append(f"{k}: 매매 권유 표현")
        n = _sentences(text)
        if not 2 <= n <= 4:
            errs.append(f"{k}: 문장 수 {n} (2~4 필요)")
    return errs


# ---------------------------------------------------------------- 템플릿 (입력값만 쓰는 고정 문장)
def template_scenario(inp: dict[str, Any]) -> dict[str, str]:
    p = inp["예측"]
    rng, prices = p["예상_수익률_범위"], p["예상_가격_범위"]
    factors = inp.get("근거_TOP5") or []
    first = factors[0] if factors else "가격·수급 지표"
    second = factors[1] if len(factors) > 1 else first
    name = inp["종목명"]
    return {
        "summary": (
            f"{name}의 향후 5거래일 상승확률은 {p['5거래일_상승확률']}로 추정됩니다. "
            f"예상 수익률 범위는 {rng['하단']}에서 {rng['상단']} 사이입니다. "
            f"관련 요인으로는 {first}, {second} 등이 있습니다."
        ),
        "bull": (
            f"예상 범위 상단에서는 5거래일 뒤 가격이 {prices['상단']} 부근까지 열려 있습니다. "
            f"관련 요인 가운데 {first} 항목이 함께 나타나고 있습니다."
        ),
        "base": (
            f"중앙 예측은 {rng['중앙']}로, 가격으로는 {prices['중앙']} 부근입니다. "
            f"범위 안의 등락 가능성을 함께 보는 것이 좋습니다."
        ),
        "bear": (
            f"예상 범위 하단에서는 가격이 {prices['하단']} 부근까지 내려갈 수 있습니다. "
            f"모델 예측에는 불확실성이 있어 하단 범위도 함께 고려해야 합니다."
        ),
    }


# ---------------------------------------------------------------- 생성
def generate_scenario(inp: dict[str, Any], llm: LLMClient | None) -> ScenarioResult:
    template = template_scenario(inp)
    if llm is None:
        return ScenarioResult(**template, generated_by="template", errors=["LLM 미설정"])
    user = json.dumps(inp, ensure_ascii=False, indent=1)
    tokens_in = tokens_out = 0
    errors: list[str] = []
    for attempt in range(2):  # 실패 시 1회 재시도
        try:
            res = llm.generate(SYSTEM_PROMPT, user)
        except Exception as e:  # LLM 장애 -> 템플릿 (FR-SC-5)
            errors.append(f"시도 {attempt + 1}: {e.__class__.__name__}")
            break
        tokens_in, tokens_out = tokens_in + res.input_tokens, tokens_out + res.output_tokens
        try:
            out = json.loads(res.text)
        except ValueError:
            errors.append(f"시도 {attempt + 1}: JSON 아님")
            continue
        problems = validate(out, inp) if isinstance(out, dict) else ["JSON 객체가 아님"]
        if not problems:
            return ScenarioResult(
                **{k: out[k].strip() for k in KEYS},
                generated_by="llm",
                llm_model=res.model,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                errors=errors,
            )
        errors.append(f"시도 {attempt + 1}: " + "; ".join(problems))
    log.warning("시나리오 템플릿 대체: %s", " | ".join(errors))
    return ScenarioResult(
        **template,
        generated_by="template",
        llm_model=llm.model,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        errors=errors,
    )
