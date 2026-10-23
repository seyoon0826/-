"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import { useToast } from "@/components/ui/Toast";
import { ApiError, apiRequest, swrFetcher } from "@/lib/api";
import { REJECT_LABEL, krw, won } from "@/lib/format";
import { isValidTick, priceLimits, stepDown, stepUp } from "@/lib/ticks";
import type { Estimate, Holding, Order } from "@/types";

/** 주문 패널 (SPEC 13.3): 매수/매도, 시장가/지정가, 수량, 가격 ±틱, 주문가능, 예상 비용, 장 상태 안내. */
export default function OrderPanel({
  token,
  code,
  name,
  refPrice,
  prevClose,
  pickedPrice,
  availableCash,
  holding,
  marketOpen,
  onPlaced,
}: {
  token: string;
  code: string;
  name: string;
  refPrice?: number;
  prevClose?: number;
  pickedPrice?: number;
  availableCash: number;
  holding?: Holding;
  marketOpen: boolean | null;
  onPlaced: () => void;
}) {
  const toast = useToast();
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [type, setType] = useState<"LIMIT" | "MARKET">("LIMIT");
  const [qty, setQty] = useState(1);
  const [price, setPrice] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 종목이 바뀌면 그 종목의 현재가로 가격 칸을 한 번 채운다 (이후 시세가 움직여도 사용자가 넣은 값은 유지)
  const filledFor = useRef<string | null>(null);
  useEffect(() => {
    if (refPrice !== undefined && filledFor.current !== code) {
      setPrice(refPrice);
      filledFor.current = code;
    }
  }, [code, refPrice]);
  useEffect(() => {
    if (pickedPrice) {
      setPrice(pickedPrice);
      setType("LIMIT");
    }
  }, [pickedPrice]);

  const limits = prevClose ? priceLimits(prevClose) : null;
  const priceOk =
    type === "MARKET" ||
    (typeof price === "number" && isValidTick(price) && (!limits || (price >= limits[0] && price <= limits[1])));
  const qtyOk = Number.isInteger(qty) && qty >= 1;

  // 예상 체결금액·수수료·세금 (GET /paper/estimate). 입력이 멈춘 뒤 0.3초 후 조회
  const [debounced, setDebounced] = useState<string | null>(null);
  const estPath =
    qtyOk && priceOk
      ? `/paper/estimate?code=${code}&side=${side}&order_type=${type}&qty=${qty}${type === "LIMIT" ? `&limit_price=${price}` : ""}`
      : null;
  useEffect(() => {
    const t = setTimeout(() => setDebounced(estPath), 300);
    return () => clearTimeout(t);
  }, [estPath]);
  const est = useSWR<Estimate>(debounced ? [debounced, token] : null, swrFetcher);

  const fee = est.data ? Number(est.data.rates.buy_fee) : 0.000176923;
  const maxQty = useMemo(() => {
    if (side === "SELL") return holding ? holding.qty - holding.reserved_qty : 0;
    const basis = type === "LIMIT" ? (typeof price === "number" ? price : 0) : limits ? limits[1] : 0; // 시장가는 상한가로 예약
    return basis > 0 ? Math.floor(availableCash / (basis * (1 + fee))) : 0;
  }, [side, type, price, limits, availableCash, holding, fee]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = { code, side, order_type: type, qty, ...(type === "LIMIT" ? { limit_price: price } : {}) };
      const res = await apiRequest<Order>("/paper/orders", {
        method: "POST",
        token,
        body,
        headers: { "Idempotency-Key": crypto.randomUUID() }, // 같은 주문이 두 번 들어가지 않게 (SPEC 7.9)
      });
      const o = res.data;
      const verb = side === "BUY" ? "매수" : "매도";
      toast(
        o.status === "FILLED"
          ? `체결 · ${name} ${verb} ${o.filled_qty}주`
          : o.queued
            ? `접수 · ${name} ${verb} ${qty}주 (다음 장 시작 시 처리)`
            : `접수 · ${name} ${verb} ${qty}주 (${o.status === "PARTIAL" ? `${o.filled_qty}주 체결, 나머지 대기` : "대기"})`,
        side === "BUY" ? "up" : "dn",
      );
      onPlaced();
    } catch (e) {
      const msg = e instanceof ApiError ? `${REJECT_LABEL[e.code] ?? e.code}: ${e.message}` : "주문하지 못했습니다";
      setError(msg);
      toast(`주문 거부 · ${msg}`, "err");
      onPlaced(); // 거부된 주문도 내역에 남는다
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel" style={{ display: "grid", gap: 12 }}>
      <div className="panel-h" style={{ marginBottom: 0 }}>
        <span>주문 · {name}</span>
        <span className="chip">{marketOpen === null ? "장 상태 확인 중" : marketOpen ? "정규장" : "장 마감"}</span>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <span className={`seg ${side === "BUY" ? "buy" : "sell"}`} role="group" aria-label="매수/매도">
          <button aria-pressed={side === "BUY"} onClick={() => setSide("BUY")}>
            매수
          </button>
          <button aria-pressed={side === "SELL"} className="sell-on" onClick={() => setSide("SELL")}>
            매도
          </button>
        </span>
        <span className="seg" role="group" aria-label="주문 종류">
          <button aria-pressed={type === "LIMIT"} onClick={() => setType("LIMIT")}>
            지정가
          </button>
          <button aria-pressed={type === "MARKET"} onClick={() => setType("MARKET")}>
            시장가
          </button>
        </span>
      </div>
      {type === "LIMIT" && (
        <label className="field">
          <span className="label">가격 {limits ? `(허용 ${won(limits[0])} ~ ${won(limits[1])})` : ""}</span>
          <span className="input-row">
            <button
              className="btn"
              aria-label="한 호가 내리기"
              onClick={() => typeof price === "number" && setPrice(stepDown(price))}
            >
              −
            </button>
            <input
              className="input"
              inputMode="numeric"
              value={price === "" ? "" : won(price)}
              onChange={(e) => {
                const v = Number(e.target.value.replace(/[^0-9]/g, ""));
                setPrice(v || "");
              }}
              aria-invalid={!priceOk}
            />
            <button
              className="btn"
              aria-label="한 호가 올리기"
              onClick={() => typeof price === "number" && setPrice(stepUp(price))}
            >
              +
            </button>
          </span>
          {!priceOk && <span className="label up">호가 단위나 가격제한폭(전일 종가 ±30%)에 맞지 않습니다</span>}
        </label>
      )}
      <label className="field">
        <span className="label">
          수량 · 주문가능 {won(Math.max(maxQty, 0))}주 {side === "BUY" ? `(가용 현금 ${krw(availableCash)})` : ""}
        </span>
        <span className="input-row">
          <input
            className="input"
            inputMode="numeric"
            value={qty}
            onChange={(e) => setQty(Number(e.target.value.replace(/[^0-9]/g, "")) || 0)}
            aria-invalid={!qtyOk}
          />
          <button className="btn" onClick={() => setQty(Math.max(maxQty, 0))}>
            최대
          </button>
        </span>
      </label>
      <div style={{ background: "var(--surface-2)", borderRadius: 8, padding: "8px 12px" }}>
        {est.data ? (
          <>
            <div className="kv">
              <span className="label">예상 체결금액</span>
              <span>{krw(est.data.amount)}</span>
            </div>
            <div className="kv">
              <span className="label">수수료{side === "SELL" ? " · 거래세" : ""}</span>
              <span>
                {krw(est.data.fee)}
                {side === "SELL" ? ` · ${krw(est.data.tax)}` : ""}
              </span>
            </div>
            <div className="kv">
              <span className="value">{side === "BUY" ? "필요 금액" : "받을 금액"}</span>
              <span className="value">{krw(est.data.total)}</span>
            </div>
            {type === "MARKET" && side === "BUY" && est.data.required_reserve != null && (
              <div className="label">
                시장가 매수는 상한가 기준 {krw(est.data.required_reserve)}을 잠시 예약했다가 체결 후 차액을 돌려줍니다.
              </div>
            )}
          </>
        ) : (
          <span className="label">수량과 가격을 넣으면 예상 비용이 나옵니다.</span>
        )}
      </div>
      {marketOpen === false && (
        <div className="label">지금은 장 외 시간입니다. 주문은 다음 장 시작(09:00) 시 처리됩니다.</div>
      )}
      {error && (
        <div className="label up" role="alert">
          {error}
        </div>
      )}
      <button
        className={`btn btn-primary ${side === "BUY" ? "btn-buy" : "btn-sell"}`}
        disabled={busy || !qtyOk || !priceOk}
        onClick={submit}
      >
        {busy ? "주문 중…" : `${type === "LIMIT" ? "지정가" : "시장가"} ${side === "BUY" ? "매수" : "매도"}`}
      </button>
    </div>
  );
}
