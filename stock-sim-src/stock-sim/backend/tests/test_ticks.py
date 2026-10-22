"""호가 단위와 가격제한폭 (SPEC 7.3, FR-MD-2)."""

import pytest

from app.services.ticks import (
    is_valid_tick,
    price_limits,
    round_down_to_tick,
    round_to_tick,
    round_up_to_tick,
    step_down,
    step_up,
    tick_size,
)


@pytest.mark.parametrize(
    "price,tick",
    [
        (1, 1),
        (1_999, 1),
        (2_000, 5),
        (4_995, 5),
        (5_000, 10),
        (19_990, 10),
        (20_000, 50),
        (49_950, 50),
        (50_000, 100),
        (199_900, 100),
        (200_000, 500),
        (499_500, 500),
        (500_000, 1_000),
        (1_021_000, 1_000),
    ],
)
def test_tick_size_boundaries(price, tick):
    assert tick_size(price) == tick


def test_valid_tick():
    assert is_valid_tick(72_100) and not is_valid_tick(72_050) and not is_valid_tick(0)
    assert is_valid_tick(1_999) and not is_valid_tick(2_003)


def test_rounding():
    assert round_down_to_tick(72_150) == 72_100
    assert round_up_to_tick(72_150) == 72_200
    assert round_to_tick(72_149) == 72_100 and round_to_tick(72_151) == 72_200


def test_step_crosses_tick_boundaries():
    assert step_up(1_999) == 2_000  # 1원 단위 구간 끝
    assert step_up(2_000) == 2_005
    assert step_down(2_000) == 1_999  # 경계에서 내려갈 때는 아래 구간 틱
    assert step_down(5_000) == 4_995
    assert step_up(49_950) == 50_000 and step_down(50_000) == 49_950
    assert step_up(72_100, 3) == 72_400


def test_price_limits_30_percent():
    low, up = price_limits(72_100)
    assert (low, up) == (50_500, 93_700)  # 50,470 -> 올림 100원 단위, 93,730 -> 내림
    assert is_valid_tick(low) and is_valid_tick(up)
    assert price_limits(10_000) == (7_000, 13_000)
