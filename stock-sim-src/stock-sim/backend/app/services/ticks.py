"""호가 단위(틱)와 가격제한폭. 값은 표(TICK_TABLE)로 관리한다 (SPEC 7.3).

TODO(verify): KRX 현행 규정 대조. 2023년 개편 후 유가증권시장 주식 기준으로 SPEC 표와 일치하는 것을
보도/증권사 안내로 확인했으나(KRX 원문은 직접 조회하지 못함), ETF/ETN 등은 단위가 다르다 (v1 범위 밖).
"""

# (상한 미만 가격, 틱). 마지막 행은 상한 없음.
TICK_TABLE: list[tuple[int | None, int]] = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (None, 1_000),
]

LIMIT_RATE_PCT = 30  # 가격제한폭 ±30% (SPEC 7.2)


def tick_size(price: int) -> int:
    for upper, tick in TICK_TABLE:
        if upper is None or price < upper:
            return tick
    return TICK_TABLE[-1][1]


def is_valid_tick(price: int) -> bool:
    return price > 0 and price % tick_size(price) == 0


def round_down_to_tick(price: int) -> int:
    t = tick_size(price)
    return (price // t) * t


def round_up_to_tick(price: int) -> int:
    t = tick_size(price)
    return -(-price // t) * t


def round_to_tick(price: float) -> int:
    """가장 가까운 호가 단위로 반올림 (예상 가격 범위 표시용, FR-PR-4)."""
    p = int(round(price))
    t = tick_size(p)
    return int(round(p / t)) * t


def step_up(price: int, n: int = 1) -> int:
    """호가를 n틱 위로 (구간 경계를 넘을 때 틱이 바뀌는 것을 반영)."""
    for _ in range(n):
        price += tick_size(price)
    return price


def step_down(price: int, n: int = 1) -> int:
    for _ in range(n):
        # 경계 가격(예: 5,000)에서 내려갈 때는 아래 구간의 틱을 쓴다
        price -= tick_size(price - 1) if price > 1 else 1
    return price


def price_limits(base_price: int) -> tuple[int, int]:
    """기준가(전일 종가) 대비 (하한가, 상한가). 상한은 틱 내림, 하한은 틱 올림.  # TODO(verify): 절사 규칙"""
    upper = round_down_to_tick(base_price * (100 + LIMIT_RATE_PCT) // 100)
    lower = round_up_to_tick(-(-base_price * (100 - LIMIT_RATE_PCT) // 100))
    return lower, upper
