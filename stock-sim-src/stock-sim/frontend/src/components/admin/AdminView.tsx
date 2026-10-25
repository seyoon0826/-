"use client";

import { useState } from "react";
import useSWR from "swr";

import AuthGate from "@/components/paper/AuthGate";
import { Empty, ErrorBox, Skeleton } from "@/components/ui/States";
import { useToast } from "@/components/ui/Toast";
import { ApiError, apiRequest, swrEnvelope, type Envelope } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { krw, pctValue, STATUS_LABEL, tone, won } from "@/lib/format";

interface Overview {
  users: { total: number; new_7d: number; suspended: number; password: number; google: number; naver: number };
  paper: {
    user_accounts: number;
    ai_accounts: number;
    orders_today: number;
    trades_today: number;
    open_orders: number;
  };
  model: { serving: string | null; latest_as_of: string | null; latest_version: string | null; ai_mode: string };
  jobs: JobRun[];
}
interface JobRun {
  id: number;
  job: string;
  status: "running" | "success" | "partial" | "failed";
  started_at: string | null;
  finished_at: string | null;
  items_ok: number;
  items_failed: number;
  error: string | null;
}
interface AdminUser {
  id: number;
  email: string | null;
  display_name: string | null;
  login_methods: string[];
  created_at: string | null;
  is_admin: boolean;
  suspended_at: string | null;
  suspend_reason: string | null;
  hidden_from_ranking: boolean;
  open_accounts?: number;
}
interface UserDetail extends AdminUser {
  accounts: {
    id: number;
    type: "USER" | "AI";
    initial_cash: number;
    cash: number;
    value: number;
    return_pct: number | null;
    positions: number;
    trades: number;
    created_at: string | null;
    closed_at: string | null;
  }[];
  recent_orders: {
    id: string;
    account_id: number;
    code: string;
    side: "BUY" | "SELL";
    order_type: string;
    qty: number;
    filled_qty: number;
    limit_price: number | null;
    status: string;
    created_at: string | null;
  }[];
}
interface AuditRow {
  id: number;
  action: string;
  admin: string;
  target_user_id: number | null;
  target: string | null;
  detail: string | null;
  created_at: string | null;
}

const METHOD_LABEL: Record<string, string> = { google: "구글", naver: "네이버", password: "이메일" };
const JOB_STATUS: Record<JobRun["status"], string> = {
  running: "실행 중",
  success: "성공",
  partial: "일부 실패",
  failed: "실패",
};
const ACTION_LABEL: Record<string, string> = {
  suspend: "이용 정지",
  unsuspend: "정지 해제",
  force_logout: "강제 로그아웃",
  hide_ranking: "랭킹에서 숨김",
  show_ranking: "랭킹에 다시 표시",
};
// 표에 들어가도록 짧게: 2026.10.25 18:07 (한국 시간)
const when = (iso: string | null) => {
  if (!iso) return "-";
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(iso))
      .map((x) => [x.type, x.value]),
  );
  return `${p.year}.${p.month}.${p.day} ${p.hour}:${p.minute}`;
};

/** 운영 화면. 질문: "서비스가 제대로 돌고 있나, 문제 있는 사용자는 누구이고 무엇을 했나?" */
export default function AdminView() {
  const { token, user, ready } = useAuth();
  const [tab, setTab] = useState<"users" | "jobs" | "audit">("users");
  const allowed = ready && !!user?.is_admin;
  const overview = useSWR<Envelope<Overview>>(allowed ? ["/admin/overview", token] : null, swrEnvelope, {
    refreshInterval: 60_000,
  });

  if (!ready) return <Skeleton lines={6} />;
  if (!user) return <AuthGate next="/admin" />;
  if (!user.is_admin)
    return (
      <section className="panel">
        <Empty title="관리자만 볼 수 있는 화면입니다">관리자 계정은 구글로 로그인해야 합니다.</Empty>
      </section>
    );

  const o = overview.data?.data;
  const failed = o?.jobs.filter((j) => j.status === "failed" || j.status === "partial") ?? [];

  return (
    <>
      <header className="screen-head">
        <div>
          <h1>관리자</h1>
          <p>사용자, 모의투자 활동, 수집·예측 작업 상태를 봅니다. 사용자 정지와 랭킹 숨김은 모두 기록에 남습니다.</p>
        </div>
      </header>

      {overview.error ? (
        <section className="panel">
          <ErrorBox message="현황을 불러오지 못했습니다." onRetry={() => overview.mutate()} />
        </section>
      ) : !o ? (
        <section className="panel">
          <Skeleton lines={4} />
        </section>
      ) : (
        <>
          {failed.length > 0 && (
            <p className="finding" role="status">
              마지막 실행이 실패한 작업: {failed.map((j) => `${j.job}(${JOB_STATUS[j.status]})`).join(", ")}. 작업 기록
              탭에서 오류를 확인하세요.
            </p>
          )}
          <section className="admin-kpis" aria-label="현황">
            <div className="panel admin-kpi">
              <h2>사용자</h2>
              <p className="admin-big">{won(o.users.total)}명</p>
              <dl>
                <dt>최근 7일 가입</dt>
                <dd>{won(o.users.new_7d)}</dd>
                <dt>정지</dt>
                <dd>{won(o.users.suspended)}</dd>
                <dt>구글 · 네이버 · 이메일</dt>
                <dd>
                  {o.users.google} · {o.users.naver} · {o.users.password}
                </dd>
              </dl>
            </div>
            <div className="panel admin-kpi">
              <h2>오늘 모의투자</h2>
              <p className="admin-big">{won(o.paper.orders_today)}건 주문</p>
              <dl>
                <dt>체결</dt>
                <dd>{won(o.paper.trades_today)}</dd>
                <dt>대기 중 주문</dt>
                <dd>{won(o.paper.open_orders)}</dd>
                <dt>계좌 (사용자 · AI)</dt>
                <dd>
                  {o.paper.user_accounts} · {o.paper.ai_accounts}
                </dd>
              </dl>
            </div>
            <div className="panel admin-kpi">
              <h2>예측 모델</h2>
              <p className="admin-big">{o.model.latest_as_of ?? "예측 없음"}</p>
              <dl>
                <dt>서빙 버전</dt>
                <dd className="admin-mono">{o.model.serving ?? "설정 안 됨"}</dd>
                <dt>최근 예측 버전</dt>
                <dd className="admin-mono">{o.model.latest_version ?? "-"}</dd>
                <dt>AI 매매 방식</dt>
                <dd>{o.model.ai_mode === "rank" ? "순위 (상위 5 매수)" : o.model.ai_mode}</dd>
              </dl>
            </div>
          </section>
        </>
      )}

      <section className="panel">
        <div className="tabs" role="tablist" aria-label="관리 항목">
          {(
            [
              ["users", "사용자"],
              ["jobs", "작업 기록"],
              ["audit", "관리 기록"],
            ] as const
          ).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </div>
        {tab === "users" && <Users token={token} />}
        {tab === "jobs" && <Jobs token={token} />}
        {tab === "audit" && <Audit token={token} />}
      </section>
    </>
  );
}

function Users({ token }: { token: string | null }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [picked, setPicked] = useState<number | null>(null);
  const list = useSWR<Envelope<{ rows: AdminUser[]; total: number }>>(
    [`/admin/users?limit=100${term ? `&q=${encodeURIComponent(term)}` : ""}`, token],
    swrEnvelope,
  );
  const rows = list.data?.data.rows ?? [];

  return (
    <div className="admin-users">
      <form
        className="admin-search"
        onSubmit={(e) => {
          e.preventDefault();
          setTerm(q.trim());
        }}
      >
        <input
          className="input"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="이름, 이메일 또는 사용자 번호"
          aria-label="사용자 검색"
        />
        <button className="btn" type="submit">
          찾기
        </button>
        {list.data && <span className="label">{won(list.data.data.total)}명</span>}
      </form>

      {list.error ? (
        <ErrorBox message="사용자 목록을 불러오지 못했습니다." onRetry={() => list.mutate()} />
      ) : !list.data ? (
        <Skeleton lines={5} />
      ) : rows.length === 0 ? (
        <Empty title={term ? `'${term}'에 맞는 사용자가 없습니다` : "사용자가 없습니다"} />
      ) : (
        <div className="table-wrap">
          <table className="table admin-table">
            <thead>
              <tr>
                <th>번호</th>
                <th>사용자</th>
                <th>로그인</th>
                <th>가입</th>
                <th>계좌</th>
                <th>상태</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => (
                <tr
                  key={u.id}
                  className={picked === u.id ? "is-picked" : undefined}
                  onClick={() => setPicked(picked === u.id ? null : u.id)}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setPicked(picked === u.id ? null : u.id);
                    }
                  }}
                  aria-expanded={picked === u.id}
                >
                  <td>{u.id}</td>
                  <td className="admin-who">
                    <b>{u.display_name ?? "(이름 없음)"}</b>
                    <span className="label">{u.email ?? "이메일 없음"}</span>
                  </td>
                  <td>{u.login_methods.map((m) => METHOD_LABEL[m] ?? m).join(", ") || "-"}</td>
                  <td>{when(u.created_at)}</td>
                  <td>{u.open_accounts ?? 0}</td>
                  <td>
                    <StatusChips u={u} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {picked != null && (
        <UserPanel
          key={picked}
          id={picked}
          token={token}
          onChanged={() => void list.mutate()}
          onClose={() => setPicked(null)}
        />
      )}
    </div>
  );
}

function StatusChips({ u }: { u: AdminUser }) {
  return (
    <span className="admin-chips">
      {u.is_admin && <span className="chip">관리자</span>}
      {u.suspended_at && <span className="chip chip-err">정지</span>}
      {u.hidden_from_ranking && <span className="chip chip-warn">랭킹 숨김</span>}
      {!u.is_admin && !u.suspended_at && !u.hidden_from_ranking && <span className="label">정상</span>}
    </span>
  );
}

function UserPanel({
  id,
  token,
  onChanged,
  onClose,
}: {
  id: number;
  token: string | null;
  onChanged: () => void;
  onClose: () => void;
}) {
  const toast = useToast();
  const detail = useSWR<Envelope<UserDetail>>([`/admin/users/${id}`, token], swrEnvelope);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const u = detail.data?.data;

  async function act(path: string, body: unknown, done: string) {
    setBusy(true);
    try {
      await apiRequest(`/admin/users/${id}/${path}`, { method: "POST", token, body });
      toast(done);
      setReason("");
      await detail.mutate();
      onChanged();
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "요청에 실패했습니다.", "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-detail" aria-label="사용자 상세">
      {detail.error ? (
        <ErrorBox message="사용자 정보를 불러오지 못했습니다." onRetry={() => detail.mutate()} />
      ) : !u ? (
        <Skeleton lines={4} />
      ) : (
        <>
          <div className="admin-detail-head">
            <div>
              <h2>
                {u.display_name ?? "(이름 없음)"} <span className="label">#{u.id}</span>
              </h2>
              <p className="label">
                {u.email ?? "이메일 없음"} · 가입 {when(u.created_at)}
              </p>
              {u.suspended_at && (
                <p className="admin-note">
                  {when(u.suspended_at)}에 정지됨{u.suspend_reason ? `: ${u.suspend_reason}` : ""}
                </p>
              )}
            </div>
            <button className="btn btn-sm" type="button" onClick={onClose}>
              닫기
            </button>
          </div>

          {u.is_admin ? (
            <p className="label">관리자 계정은 정지하거나 숨길 수 없습니다.</p>
          ) : (
            <div className="admin-actions">
              {u.suspended_at ? (
                <button
                  className="btn"
                  type="button"
                  disabled={busy}
                  onClick={() => act("unsuspend", undefined, "정지를 풀었습니다.")}
                >
                  정지 해제
                </button>
              ) : (
                <form
                  className="admin-suspend"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (
                      window.confirm(
                        `${u.display_name ?? u.email ?? `#${u.id}`} 사용자를 정지할까요? 바로 로그아웃됩니다.`,
                      )
                    )
                      void act("suspend", { reason: reason.trim() || null }, "사용자를 정지했습니다.");
                  }}
                >
                  <input
                    className="input"
                    value={reason}
                    maxLength={200}
                    onChange={(e) => setReason(e.target.value)}
                    placeholder="정지 사유 (기록에 남습니다)"
                    aria-label="정지 사유"
                  />
                  <button className="btn btn-danger" type="submit" disabled={busy}>
                    이용 정지
                  </button>
                </form>
              )}
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() =>
                  act(
                    "ranking",
                    { hidden: !u.hidden_from_ranking },
                    u.hidden_from_ranking ? "랭킹에 다시 표시합니다." : "랭킹에서 숨겼습니다.",
                  )
                }
              >
                {u.hidden_from_ranking ? "랭킹에 다시 표시" : "랭킹에서 숨기기"}
              </button>
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => act("logout", undefined, "모든 기기에서 로그아웃시켰습니다.")}
              >
                모든 기기 로그아웃
              </button>
            </div>
          )}

          <h3 className="admin-sub">계좌</h3>
          {u.accounts.length === 0 ? (
            <p className="label">아직 모의투자 계좌가 없습니다.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>계좌</th>
                    <th>처음 자금</th>
                    <th>평가금액</th>
                    <th>수익률</th>
                    <th>보유 종목</th>
                    <th>체결</th>
                    <th>개설</th>
                  </tr>
                </thead>
                <tbody>
                  {u.accounts.map((a) => (
                    <tr key={a.id}>
                      <td>
                        {a.type === "AI" ? "AI" : "내 계좌"} #{a.id}
                        {a.closed_at && <span className="label"> (닫힘)</span>}
                      </td>
                      <td>{krw(a.initial_cash)}</td>
                      <td>{krw(a.value)}</td>
                      <td className={a.return_pct == null ? "" : tone(a.return_pct)}>
                        {a.return_pct == null ? "-" : pctValue(a.return_pct)}
                      </td>
                      <td>{a.positions}</td>
                      <td>{a.trades}</td>
                      <td>{when(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h3 className="admin-sub">최근 주문</h3>
          {u.recent_orders.length === 0 ? (
            <p className="label">주문이 없습니다.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>시각</th>
                    <th>종목</th>
                    <th>구분</th>
                    <th>수량</th>
                    <th>지정가</th>
                    <th>상태</th>
                  </tr>
                </thead>
                <tbody>
                  {u.recent_orders.map((o) => (
                    <tr key={o.id}>
                      <td>{when(o.created_at)}</td>
                      <td>{o.code}</td>
                      <td className={o.side === "BUY" ? "up" : "dn"}>
                        {o.side === "BUY" ? "매수" : "매도"} {o.order_type === "MARKET" ? "시장가" : "지정가"}
                      </td>
                      <td>
                        {won(o.filled_qty)}/{won(o.qty)}
                      </td>
                      <td>{o.limit_price == null ? "-" : won(o.limit_price)}</td>
                      <td>{STATUS_LABEL[o.status] ?? o.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function Jobs({ token }: { token: string | null }) {
  const jobs = useSWR<Envelope<JobRun[]>>(["/admin/jobs?limit=100", token], swrEnvelope, { refreshInterval: 60_000 });
  if (jobs.error) return <ErrorBox message="작업 기록을 불러오지 못했습니다." onRetry={() => jobs.mutate()} />;
  if (!jobs.data) return <Skeleton lines={5} />;
  if (jobs.data.data.length === 0)
    return <Empty title="아직 실행된 작업이 없습니다">스케줄러가 돌면 여기에 쌓입니다.</Empty>;
  return (
    <div className="table-wrap">
      <table className="table admin-table">
        <thead>
          <tr>
            <th>작업</th>
            <th>시작</th>
            <th>끝</th>
            <th>성공</th>
            <th>실패</th>
            <th>상태</th>
          </tr>
        </thead>
        <tbody>
          {jobs.data.data.map((j) => (
            <tr key={j.id} title={j.error ?? undefined}>
              <td className="admin-who">
                <b>{j.job}</b>
                {j.error && <span className="label admin-error">{j.error}</span>}
              </td>
              <td>{when(j.started_at)}</td>
              <td>{when(j.finished_at)}</td>
              <td>{won(j.items_ok)}</td>
              <td>{won(j.items_failed)}</td>
              <td>
                <span
                  className={`chip ${j.status === "failed" ? "chip-err" : j.status === "partial" ? "chip-warn" : ""}`}
                >
                  {JOB_STATUS[j.status]}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Audit({ token }: { token: string | null }) {
  const log = useSWR<Envelope<AuditRow[]>>(["/admin/audit?limit=200", token], swrEnvelope);
  if (log.error) return <ErrorBox message="관리 기록을 불러오지 못했습니다." onRetry={() => log.mutate()} />;
  if (!log.data) return <Skeleton lines={5} />;
  if (log.data.data.length === 0)
    return <Empty title="아직 관리 기록이 없습니다">사용자를 정지하거나 숨기면 여기에 남습니다.</Empty>;
  return (
    <div className="table-wrap">
      <table className="table admin-table">
        <thead>
          <tr>
            <th>시각</th>
            <th>관리자</th>
            <th>한 일</th>
            <th>대상</th>
            <th>사유</th>
          </tr>
        </thead>
        <tbody>
          {log.data.data.map((r) => (
            <tr key={r.id}>
              <td>{when(r.created_at)}</td>
              <td>{r.admin}</td>
              <td>{ACTION_LABEL[r.action] ?? r.action}</td>
              <td>{r.target ? `${r.target} #${r.target_user_id}` : "-"}</td>
              <td>{r.detail ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
