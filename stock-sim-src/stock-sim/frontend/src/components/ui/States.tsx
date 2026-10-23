// 로딩/에러/빈 상태 (SPEC 13.5)
import type { ReactNode } from "react";

export function Skeleton({ lines = 3, height = 14 }: { lines?: number; height?: number }) {
  return (
    <div aria-busy="true" aria-label="불러오는 중" style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton" style={{ height, width: `${90 - (i % 3) * 15}%` }} />
      ))}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-box" role="alert">
      <p style={{ margin: 0 }}>{message}</p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          다시 시도
        </button>
      )}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state-box">
      <p className="value" style={{ margin: 0 }}>
        {title}
      </p>
      {children && <p className="label" style={{ margin: 0 }}>{children}</p>}
    </div>
  );
}
