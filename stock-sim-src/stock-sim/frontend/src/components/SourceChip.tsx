/** 화면의 데이터 출처 표시: 실데이터(API) / 예시 데이터 */
export default function SourceChip({ live, label }: { live: boolean; label?: string }) {
  return (
    <span className={`chip ${live ? "chip-live" : ""}`} title={live ? "백엔드 API 에서 받은 실제 데이터" : "API 미연결 또는 미구현: 예시 데이터"}>
      {label ?? (live ? "실데이터" : "예시 데이터")}
    </span>
  );
}
