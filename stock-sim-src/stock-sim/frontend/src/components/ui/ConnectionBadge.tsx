import type { ConnState } from "@/lib/ws";
import type { WsStatusMsg } from "@/types";

const LABEL: Record<ConnState, string> = { open: "실시간 연결됨", connecting: "연결 중…", closed: "연결 끊김" };

/** 실시간 연결 상태 + 장 상태 + 공급원 (SPEC 13.1, 14: degraded 표시) */
export default function ConnectionBadge({ conn, status }: { conn: ConnState; status?: WsStatusMsg | null }) {
  const cls = conn === "open" ? (status?.degraded ? "chip-warn" : "chip-live") : conn === "closed" ? "chip-err" : "";
  return (
    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}>
      <span className={`chip ${cls}`} role="status">
        {status?.degraded && conn === "open" ? "지연 시세(대체 경로)" : LABEL[conn]}
      </span>
      {status && <span className="chip">{status.market === "open" ? "정규장 운영 중" : "장 마감"}</span>}
      {status && status.provider !== "kis" && (
        <span className="chip" title="KIS 실시간 키가 없으면 종가 또는 재생 데이터로 동작합니다">
          {status.provider === "pykrx" ? "종가 기준 시세" : "재생 시세"}
        </span>
      )}
    </span>
  );
}

export function DisconnectBanner({ conn }: { conn: ConnState }) {
  if (conn !== "closed") return null;
  return (
    <div className="banner" role="alert">
      실시간 연결이 끊겼습니다. 자동으로 다시 연결하는 중이며, 그동안 마지막 가격을 보여줍니다.
    </div>
  );
}
