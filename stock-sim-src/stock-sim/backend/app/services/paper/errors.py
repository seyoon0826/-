"""모의투자 에러 코드 (SPEC 7.2, 10.5)."""

from __future__ import annotations

UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
INVALID_QTY = "INVALID_QTY"
INVALID_TICK = "INVALID_TICK"
PRICE_OUT_OF_LIMIT = "PRICE_OUT_OF_LIMIT"
INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
INSUFFICIENT_QTY = "INSUFFICIENT_QTY"
ORDER_NOT_CANCELABLE = "ORDER_NOT_CANCELABLE"
ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
INVALID_REQUEST = "INVALID_REQUEST"

MESSAGES = {
    UNKNOWN_SYMBOL: "거래할 수 없는 종목입니다.",
    INVALID_QTY: "수량은 1 이상의 정수이고 1회 최대 수량 이하여야 합니다.",
    INVALID_TICK: "지정가가 호가 단위에 맞지 않습니다.",
    PRICE_OUT_OF_LIMIT: "지정가가 가격제한폭(전일 종가 ±30%)을 벗어났습니다.",
    INSUFFICIENT_CASH: "주문 가능 현금이 부족합니다.",
    INSUFFICIENT_QTY: "매도 가능 수량이 부족합니다.",
    ORDER_NOT_CANCELABLE: "취소할 수 없는 주문입니다.",
    ACCOUNT_NOT_FOUND: "계좌가 없습니다.",
    UPSTREAM_UNAVAILABLE: "시세가 최신이 아니어서 시장가 주문을 받을 수 없습니다.",
    INVALID_REQUEST: "잘못된 주문 요청입니다.",
}


class PaperError(Exception):
    def __init__(self, code: str, status: int = 422, message: str | None = None) -> None:
        super().__init__(code)
        self.code, self.status = code, status
        self.message = message or MESSAGES.get(code, code)
