import { won } from "@/lib/format";
import type { BookLevel } from "@/types";

/** 10단 호가창: 잔량 막대, 가격을 누르면 주문 가격으로 (SPEC 13.3). 매도호가는 위(높은 가격부터), 매수호가는 아래. */
export default function Orderbook({
  asks,
  bids,
  price,
  onPick,
}: {
  asks: BookLevel[];
  bids: BookLevel[];
  price?: number;
  onPick: (p: number) => void;
}) {
  const max = Math.max(1, ...asks.map((a) => a.qty), ...bids.map((b) => b.qty));
  const askRows = [...asks].slice(0, 10).reverse();
  return (
    <table className="book" aria-label="호가창">
      <tbody>
        {askRows.map((a) => (
          <tr key={`a${a.price}`} className="ask" onClick={() => onPick(a.price)}>
            <td style={{ width: "50%", textAlign: "right" }}>
              <span
                className="qtybar"
                style={{ right: 0, width: `${(a.qty / max) * 100}%`, background: "var(--dn)" }}
              />
              {won(a.qty)}
            </td>
            <td className="p" style={{ fontWeight: a.price === price ? 700 : 500 }}>
              {won(a.price)}
            </td>
          </tr>
        ))}
        {bids.slice(0, 10).map((b, i) => (
          <tr key={`b${b.price}`} className={`bid ${i === 0 ? "mid" : ""}`} onClick={() => onPick(b.price)}>
            <td className="p" style={{ textAlign: "right", fontWeight: b.price === price ? 700 : 500 }}>
              {won(b.price)}
            </td>
            <td style={{ width: "50%" }}>
              <span className="qtybar" style={{ left: 0, width: `${(b.qty / max) * 100}%`, background: "var(--up)" }} />
              {won(b.qty)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
