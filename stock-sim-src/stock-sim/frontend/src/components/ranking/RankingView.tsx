"use client";

import Link from "next/link";
import useSWR from "swr";

import { signedPct } from "@/components/predict/Forecast";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { swrEnvelope, type Envelope } from "@/lib/api";
import { useToken } from "@/lib/auth";
import { krw, won } from "@/lib/format";

interface Row {
  rank: number;
  name: string;
  return_pct: number;
  ai_return_pct: number | null;
  initial_cash: number;
  since: string;
  trades: number;
  is_me: boolean;
}
interface Board {
  rows: Row[];
  total: number;
  me: Row | null;
}

const pct = (v: number) => signedPct(v / 100, 2);
const toneOf = (v: number) => (v > 0 ? "up" : v < 0 ? "dn" : "");

/** 모의투자 수익률 랭킹. 질문: "누가 가장 잘하고 있고, 나는 몇 위인가?" (이름은 가려서 보여준다) */
export default function RankingView() {
  const [token, ready] = useToken();
  const board = useSWR<Envelope<Board>>(ready ? ["/paper/leaderboard?limit=100", token] : null, swrEnvelope, {
    refreshInterval: 60_000,
  });
  const data = board.data?.data;
  const me = data?.me;

  return (
    <>
      <header className="screen-head">
        <div>
          <h1>수익률 랭킹</h1>
          <p>
            모의투자 계좌의 처음 자금 대비 수익률 순위입니다. 최근 종가로 평가하고, 한 번 이상 체결된 계좌만 올라갑니다.
          </p>
        </div>
        <div className="head-actions">
          <Link className="btn" href="/paper">
            모의투자 하러 가기
          </Link>
        </div>
      </header>

      {ready && me ? (
        <p className="finding">
          나는 {data!.total}명 중 <b>{me.rank}위</b>, 수익률{" "}
          <b className={toneOf(me.return_pct)}>{pct(me.return_pct)}</b>
          {me.ai_return_pct != null && (
            <>
              입니다. 같은 날 같은 돈으로 시작한 내 AI 계좌는{" "}
              <b className={toneOf(me.ai_return_pct)}>{pct(me.ai_return_pct)}</b>
              {me.return_pct >= me.ai_return_pct ? "로 AI보다 앞서 있습니다." : "로 AI가 앞서 있습니다."}
            </>
          )}
        </p>
      ) : ready && !token ? (
        <p className="finding">
          로그인하고 첫 주문을 넣으면 내 순위가 여기에 나옵니다. <Link href="/paper">로그인하기</Link>
        </p>
      ) : null}

      <section className="panel" aria-labelledby="board-h">
        <div className="panel-h">
          <span id="board-h">전체 순위</span>
          <span>{data ? `${data.total}명 참여` : ""}</span>
        </div>
        {!ready || board.isLoading ? (
          <Skeleton lines={8} />
        ) : board.error ? (
          <ErrorBox message="랭킹을 불러오지 못했습니다." onRetry={() => board.mutate()} />
        ) : !data || data.rows.length === 0 ? (
          <Empty title="아직 순위에 오른 계좌가 없습니다">
            모의투자 계좌를 만들고 첫 주문이 체결되면 순위에 올라갑니다.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table board-table">
              <thead>
                <tr>
                  <th>순위</th>
                  <th>이름</th>
                  <th>수익률</th>
                  <th title="같은 날, 같은 처음 자금으로 시작한 그 사람의 AI 계좌 수익률">그 사람의 AI</th>
                  <th>처음 자금</th>
                  <th>체결</th>
                  <th>시작일</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r, i) => (
                  <tr key={`${r.rank}-${i}`} className={r.is_me ? "is-me" : r.rank <= 3 ? "is-top" : undefined}>
                    <td className="rank-cell">
                      <span className="rank-num">{r.rank}</span>
                    </td>
                    <td>
                      {r.name}
                      {r.is_me && <span className="me-tag"> 나</span>}
                    </td>
                    <td>
                      <b className={toneOf(r.return_pct)}>{pct(r.return_pct)}</b>
                    </td>
                    <td className={r.ai_return_pct == null ? "label" : toneOf(r.ai_return_pct)}>
                      {r.ai_return_pct == null ? "-" : pct(r.ai_return_pct)}
                    </td>
                    <td>{krw(r.initial_cash)}</td>
                    <td>{won(r.trades)}회</td>
                    <td className="label">{r.since}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="table-note">
          이름은 앞 글자만 보이게 가립니다. 수익률은 현금과 보유 주식을 최근 종가로 평가한 값이며 수수료와 세금이 빠진
          뒤의 금액입니다. 계좌를 초기화하면 새 계좌 기준으로 다시 계산합니다.
        </p>
      </section>
    </>
  );
}
