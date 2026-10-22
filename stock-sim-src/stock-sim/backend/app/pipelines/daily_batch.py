"""일일 예측 배치 (SPEC 6.3 FR-PR-1, 12): 평일 거래일 15:40 가격 수집 뒤에 실행한다.

결과 채우기 -> 피처 -> 예측 -> SHAP -> 관련 뉴스 -> 시나리오 -> 성적표 지표.
누락된 날은 다음 실행 때 채운다(catch-up).
"""

from __future__ import annotations

import logging
from datetime import date

from app.core.config import Settings, settings
from app.core.db import SessionLocal
from app.ml.explain import Explainer
from app.ml.infer import ModelBundle
from app.pipelines.common import JobResult, SessionFactory
from app.services.prediction_service import BatchContext, run_daily_batch
from app.services.scenario_service import default_llm

log = logging.getLogger(__name__)


def build_context(cfg: Settings = settings) -> BatchContext | None:
    """MODEL_VERSION 이 없으면 None (배치 건너뜀). 모델 산출물이 서빙 피처와 다르면 예외."""
    if not cfg.model_version:
        log.warning("MODEL_VERSION 미설정: 예측 배치를 건너뜁니다")
        return None
    bundle = ModelBundle.load(cfg.model_dir, cfg.model_version)
    llm = default_llm(cfg.anthropic_api_key, cfg.llm_model)
    if llm is None:
        log.warning("ANTHROPIC_API_KEY 또는 LLM_MODEL 없음: 시나리오는 템플릿 문장으로 만듭니다")
    return BatchContext(bundle, Explainer(bundle), llm)


def run(
    session_factory: SessionFactory = SessionLocal, as_of: date | None = None, ctx: BatchContext | None = None
) -> JobResult | None:
    ctx = ctx or build_context()
    if ctx is None:
        return None
    return run_daily_batch(session_factory, ctx, settings.model_dir, as_of)


if __name__ == "__main__":  # pragma: no cover  (수동 실행: python -m app.pipelines.daily_batch)
    from dotenv import load_dotenv

    load_dotenv("../.env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    r = run()
    print(r.status() if r else "건너뜀", r.ok if r else "", r.failed if r else "")
