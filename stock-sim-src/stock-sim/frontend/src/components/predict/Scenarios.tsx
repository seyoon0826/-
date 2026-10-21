import type { Scenarios as ScenarioData } from "@/types";

export default function Scenarios({ data }: { data: ScenarioData }) {
  return (
    <div className="panel">
      <div className="panel-h">
        <span>시나리오</span>
        <span className="chip">{data.generated_by === "llm" ? "AI 생성" : "템플릿 문장"}</span>
      </div>
      <p style={{ margin: "0 0 12px", fontSize: 13 }}>{data.summary}</p>
      <div className="grid-3">
        <div className="scn bull">
          <div className="value up">강세</div>
          <p>{data.bull}</p>
        </div>
        <div className="scn">
          <div className="value">기본</div>
          <p>{data.base}</p>
        </div>
        <div className="scn bear">
          <div className="value dn">약세</div>
          <p>{data.bear}</p>
        </div>
      </div>
    </div>
  );
}
