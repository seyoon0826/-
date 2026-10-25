"use client";

// 소개 페이지. taste-skill 방향: Colour chapters (seed 619199975, .taste-skill/home2).
// 아이디어: 한국 시장의 색(상승 빨강, 하락 파랑)과 하루의 순서(15:40 예측 → 밤 → 09:05 AI 매수 → 5거래일)가
// 페이지의 색 챕터가 된다. 직접 해보는 것: 제목 자리에 종목 이름을 쳐 넣으면 그 종목의 순위와 범위가 바로 나온다.
// 화면 조각(data-ui)은 모두 실제 API 데이터로 그린다.
import Image from "next/image";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";

import { direction, RangeBar, signedPct } from "@/components/predict/Forecast";
import { TOP_N } from "@/components/predict/RankLadder";
import { swrEnvelope, swrFetcher, type Envelope } from "@/lib/api";
import { pct0 } from "@/lib/format";
import type { HistoryItem, StockSummary } from "@/types";

const CASH = [10_000_000, 30_000_000, 50_000_000, 100_000_000];
const cashLabel = (c: number) => (c >= 100_000_000 ? "1억 원" : `${c / 10_000_000}천만 원`);
const shortDate = (iso: string) => {
  const [, m, d] = iso.split("-").map(Number);
  return `${m}월 ${d}일`;
};

/** 화면에 들어올 때 한 번, 안의 .rv 요소들이 차례로 떠오른다 (스크립트가 없으면 처음부터 보인다) */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.classList.add("rv-armed");
    const io = new IntersectionObserver(
      (es) =>
        es.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("rv-in");
            io.unobserve(e.target);
          }
        }),
      { threshold: 0.12 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return ref;
}

// ---------------------------------------------------------------- 1. 첫 화면: 깎인 무대 위의 오늘 순위판
function RankBoard({ stocks }: { stocks: StockSummary[] }) {
  const ranked = useMemo(() => stocks.filter((s) => s.rank != null).sort((a, b) => a.rank! - b.rank!), [stocks]);
  const scale = useMemo(
    () => Math.max(0.03, ...ranked.flatMap((s) => [Math.abs(s.q10 ?? 0), Math.abs(s.q90 ?? 0)])) * 1.05,
    [ranked],
  );
  if (!ranked.length) return <div className="board board-msg">오늘의 순위는 장 마감(15:40) 뒤에 나옵니다.</div>;
  return (
    <div className="board" data-ui>
      <div className="board-head">
        <b>예측 순위</b>
        <span>{ranked[0].as_of_date ? `${shortDate(ranked[0].as_of_date)} 종가 기준` : ""}</span>
      </div>
      <table className="hb-table">
        <thead>
          <tr>
            <th>순위</th>
            <th>종목</th>
            <th>오를 확률</th>
            <th>5거래일 뒤 범위</th>
          </tr>
        </thead>
        <tbody>
          {ranked.slice(0, 8).map((s) => (
            <tr key={s.code}>
              <td className={s.rank! <= TOP_N ? "b-top" : ""}>{s.rank}</td>
              <td>
                <Link href={`/predict/${s.code}`}>{s.name}</Link>
              </td>
              <td>{s.expected ? pct0(s.expected.up_ratio) : "-"}</td>
              <td className="b-range">
                {s.q10 != null && s.q50 != null && s.q90 != null && (
                  <>
                    <span className="dn">{signedPct(s.q10, 0)}</span>
                    <RangeBar q10={s.q10} q50={s.q50} q90={s.q90} scale={scale} />
                    <span className="up">{signedPct(s.q90, 0)}</span>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Hero({ stocks, error }: { stocks?: StockSummary[]; error: boolean }) {
  return (
    <section className="ch ch-sky hero" aria-labelledby="hero-h">
      <div className="hero-copy">
        <h1 id="hero-h" className="hero-h">
          <span className="line">
            <span>매일 장 마감 뒤,</span>
          </span>
          <span className="line">
            <span>30종목을 줄 세웁니다</span>
          </span>
        </h1>
        <p className="hero-p">
          코스피 대형주의 5거래일 예측을 보고, 같은 돈으로 시작한 내 AI와 모의투자 수익률을 겨룹니다.
        </p>
        <Link className="btn-y" href="/predict">
          오늘의 예측 순위
        </Link>
      </div>
      <div className="stage">
        {error ? (
          <div className="board board-msg">순위를 불러오지 못했습니다. 잠시 뒤 새로고침하세요.</div>
        ) : !stocks ? (
          <div className="board board-msg">오늘의 순위를 불러오는 중입니다.</div>
        ) : (
          <RankBoard stocks={stocks} />
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 2. 제목 자리에 종목 이름을 쳐 넣는다
function TypeAStock({ stocks }: { stocks: StockSummary[] }) {
  const [q, setQ] = useState("기아");
  const term = q.trim();
  // 정확히 같은 이름/코드 > 이름이 그 글자로 시작 > 이름에 포함. 여럿이면 오늘 순위가 높은 종목
  // ("삼성"을 치면 코드 순서로 먼저 오는 삼성화재가 아니라 삼성전자 쪽이 나오게)
  const byRank = (a: StockSummary, b: StockSummary) => (a.rank ?? 99) - (b.rank ?? 99);
  const hit = !term
    ? undefined
    : (stocks.find((s) => s.name === term || s.code === term) ??
      [...stocks].filter((s) => s.name.startsWith(term) || s.code.startsWith(term)).sort(byRank)[0] ??
      [...stocks].filter((s) => s.name.includes(term)).sort(byRank)[0]);
  const d = hit?.expected ? direction(hit.expected.up_ratio) : null;
  const examples = ["삼성전자", "SK하이닉스", "현대차"];
  return (
    <section className="ch ch-pink typer" aria-labelledby="typer-h">
      <h2 id="typer-h" className="typer-h">
        종목 이름을 쳐 보세요
      </h2>
      <label className="typer-line" data-ui>
        <span className="sr-only">종목 이름 또는 코드</span>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          placeholder="종목 이름"
        />
      </label>
      <div className="answer" data-ui aria-live="polite">
        {!term ? (
          <p className="answer-msg">종목 이름이나 6자리 코드를 쳐 보세요.</p>
        ) : !hit ? (
          <p className="answer-msg">
            &lsquo;{term}&rsquo;은 오늘의 30종목에 없습니다. 코스피 시가총액 상위 30종목만 예측합니다.
          </p>
        ) : (
          <>
            <div className="answer-row">
              <span>{hit.name} 오늘 순위</span>
              <b>
                {hit.rank ?? "-"}위 <small>/ {hit.rank_of ?? 30}종목</small>
              </b>
            </div>
            <div className="answer-row">
              <span>5거래일 뒤 오를 확률</span>
              <b className={d?.tone}>{hit.expected ? `${pct0(hit.expected.up_ratio)}, ${d?.word}` : "-"}</b>
            </div>
            <div className="answer-row">
              <span>10번 중 8번은 이 안에서</span>
              <b>
                {hit.q10 != null && hit.q90 != null ? (
                  <>
                    <span className="dn">{signedPct(hit.q10)}</span> ~ <span className="up">{signedPct(hit.q90)}</span>
                  </>
                ) : (
                  "-"
                )}
              </b>
            </div>
            <Link className="answer-link" href={`/predict/${hit.code}`}>
              {hit.name} 근거와 차트 보기
            </Link>
          </>
        )}
      </div>
      <div className="examples" aria-label="예시 종목">
        {examples.map((x) => (
          <button key={x} type="button" onClick={() => setQ(x)} aria-pressed={q === x}>
            {x}
          </button>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 3. 하루를 막대 하나로 (실제 걸리는 시간에 비례)
const STAGES = [
  { dur: "2분", minutes: 2, tone: "sky", clause: "먼저, 15:40 장이 끝나면 2분 안에 30종목 예측과 근거를 만듭니다." },
  {
    dur: "밤사이 17시간",
    minutes: 1035,
    tone: "pink",
    clause: "그동안 모든 계좌를 종가로 평가하고 수익률 순위를 고칩니다.",
  },
  {
    dur: "5분",
    minutes: 5,
    tone: "yellow",
    clause: "다음 날 09:00 밤새 넣은 주문을 처리하고, 09:05 내 AI가 상위 5종목을 삽니다.",
  },
  {
    dur: "5거래일",
    minutes: 1950,
    tone: "white",
    clause: "그리고 5거래일 뒤 결과를 예측 옆에 붙입니다. 맞혔든 틀렸든.",
  },
];

function DayBar() {
  const ref = useReveal<HTMLElement>();
  const total = STAGES.reduce((a, s) => a + s.minutes, 0);
  // 실제 비율로는 2분과 5분이 보이지 않으므로 최소 12% 를 준다
  const grow = STAGES.map((s) => Math.max(s.minutes / total, 0.12));
  return (
    <section className="ch ch-blue daybar" ref={ref} aria-labelledby="day-h">
      <h2 id="day-h" className="ch-h rv">
        하루는 이렇게 돕니다
      </h2>
      <div className="dbar rv" data-ui aria-hidden>
        {STAGES.map((s, i) => (
          <div key={s.dur} className={`dseg dseg-${s.tone}`} style={{ flexGrow: grow[i] }}>
            <span className="dseg-dur">{s.dur}</span>
          </div>
        ))}
      </div>
      <ol className="day-clauses rv">
        {STAGES.map((s) => (
          <li key={s.dur}>
            <span className={`swatch dseg-${s.tone}`} aria-hidden />
            <b>{s.dur}</b>
            <span>{s.clause}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------- 4. 지난 예측과 결과: 접히는 카드
function PastCards({ stocks }: { stocks: StockSummary[] }) {
  const pick = stocks.find((s) => s.code === "000270") ?? stocks.find((s) => s.rank === 1);
  const hist = useSWR<Envelope<HistoryItem[]>>(
    pick ? [`/stocks/${pick.code}/prediction/history?limit=30`, null] : null,
    swrEnvelope,
  );
  const items = useMemo(() => (hist.data?.data ?? []).filter((h) => h.outcome).slice(0, 5), [hist.data]);
  const wrap = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const el = wrap.current;
    if (!el || items.length < 2) return;
    const onScroll = () => {
      if (window.innerWidth <= 720) return; // 휴대폰은 카드를 그냥 쌓는다
      const r = el.getBoundingClientRect();
      const span = r.height - window.innerHeight;
      const p = span > 0 ? Math.min(Math.max(-r.top / span, 0), 0.999) : 0;
      setActive(Math.floor(p * items.length));
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [items.length]);
  if (!pick || items.length === 0) return null;
  const hits = items.filter((h) => h.outcome!.in_range).length;
  return (
    <section className="ch ch-red past" aria-labelledby="past-h">
      <div className="past-pin" ref={wrap} style={{ ["--n" as string]: items.length }}>
        <div className="past-stage">
          <h2 id="past-h" className="ch-h">
            {pick.name}의 지난 예측
          </h2>
          <div className="deck">
            {items.map((h, i) => {
              const o = h.outcome!;
              const state = i < active ? "spine" : i === active ? "open" : "next";
              return (
                <article
                  key={h.as_of_date}
                  className={`pcard tone-${i % 3} is-${state}`}
                  onClick={() => state === "spine" && setActive(i)}
                  onKeyDown={(e) => state === "spine" && (e.key === "Enter" || e.key === " ") && setActive(i)}
                  tabIndex={state === "spine" ? 0 : undefined}
                  role={state === "spine" ? "button" : undefined}
                  aria-label={state === "spine" ? `${shortDate(h.as_of_date)} 예측 펼치기` : undefined}
                >
                  {state === "spine" ? (
                    <h3 className="spine-t">{shortDate(h.as_of_date)}</h3>
                  ) : (
                    <h3>
                      {shortDate(h.as_of_date)}에 본 범위 {signedPct(h.range.q10)} ~ {signedPct(h.range.q90)}
                    </h3>
                  )}
                  <p>
                    {shortDate(o.target_date)}까지 실제 {signedPct(o.actual_return)}.{" "}
                    {o.in_range ? "범위 안에서 끝났습니다." : "범위를 벗어났습니다."}
                  </p>
                  <div className="pcard-foot">
                    <span>{o.in_range ? "범위 적중" : "범위 빗나감"}</span>
                    <Link href={`/predict/${pick.code}`}>근거 보기</Link>
                  </div>
                </article>
              );
            })}
          </div>
          <p className="past-sum">
            최근 {items.length}번 중 {hits}번이 예상 범위 안에서 끝났습니다.{" "}
            <Link href="/performance">모델 성적표</Link>
          </p>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 5. 30종목 이름이 흘러간다
function Names({ stocks }: { stocks: StockSummary[] }) {
  const ref = useReveal<HTMLDivElement>();
  const names = [...stocks].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)).map((s) => s.name);
  const row = (copy: number) => (
    <span className="names-run" aria-hidden={copy > 0}>
      {names.map((n) => (
        <span key={n}>
          {n}
          <i aria-hidden />
        </span>
      ))}
    </span>
  );
  return (
    <section className="names" aria-labelledby="names-h">
      {names.length > 0 && (
        <div className="names-band ch-yellow">
          <div className="names-track">
            {row(0)}
            {row(1)}
          </div>
        </div>
      )}
      <div className="names-after ch-sky" ref={ref}>
        <div className="names-text">
          <h2 id="names-h" className="ch-h rv">
            매일 줄 세우는 30종목
          </h2>
          <p className="rv">
            코스피 시가총액 상위 30종목입니다(우선주 제외). 학습에는 2018년부터 그 시점마다 상위 100종목에 들었던
            180종목의 기록을 씁니다. 지금 살아남은 종목만 고르면 생기는 착시를 피하려는 것입니다.
          </p>
        </div>
        <figure className="names-photo">
          <Image
            src="/home/korea-exchange.jpg"
            width={1920}
            height={1440}
            sizes="(max-width: 720px) 100vw, 40vw"
            alt="부산 문현동 한국거래소 본사 건물. 외벽에 KRX 한국거래소 글자가 세로로 붙어 있다"
          />
          <figcaption>시세, 수급, PER·PBR은 한국거래소가 매일 내는 숫자입니다.</figcaption>
        </figure>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- 6. 지금 수익률 순위
interface Board {
  rows: { rank: number; name: string; return_pct: number; ai_return_pct: number | null }[];
  total: number;
}
function Leaders() {
  const ref = useReveal<HTMLElement>();
  const lb = useSWR<Envelope<Board>>(["/paper/leaderboard?limit=8", null], swrEnvelope);
  const rows = lb.data?.data.rows ?? [];
  const vid = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) vid.current?.pause();
  }, []);
  return (
    <section className="ch ch-yellow leaders" ref={ref} aria-labelledby="lead-h">
      <div className="lead-text">
        <h2 id="lead-h" className="ch-h rv">
          지금 수익률 순위
        </h2>
        {rows.length === 0 ? (
          <p className="lead-empty rv">
            아직 순위에 오른 계좌가 없습니다. 첫 주문이 체결되면 이 자리에 이름이 올라갑니다.
          </p>
        ) : (
          <ol className="lead-list rv">
            {rows.map((r, i) => (
              <li key={`${r.rank}-${i}`}>
                <span>
                  {r.rank}위 {r.name}
                </span>
                <b className={r.return_pct > 0 ? "up" : r.return_pct < 0 ? "dn" : ""}>
                  {signedPct(r.return_pct / 100, 2)}
                </b>
                <small>그 사람의 AI {r.ai_return_pct == null ? "-" : signedPct(r.ai_return_pct / 100, 2)}</small>
              </li>
            ))}
          </ol>
        )}
        <p className="lead-note rv">
          처음 자금 대비 수익률을 최근 종가로 평가합니다. 이름은 앞 글자만 보입니다.{" "}
          <Link href="/ranking">전체 순위</Link>
        </p>
      </div>
      <figure className="lead-media">
        <video
          ref={vid}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          poster="/home/seoul-from-63.jpg"
          width={854}
          height={480}
          aria-label="여의도 63빌딩에서 내려다본 한강과 서울"
        >
          <source src="/home/seoul-from-63.webm" type="video/webm" />
        </video>
        <figcaption>여의도 63빌딩에서 본 서울</figcaption>
      </figure>
    </section>
  );
}

// ---------------------------------------------------------------- 7. 빈칸이 있는 문장
function Start() {
  const [cash, setCash] = useState(CASH[0]);
  return (
    <section className="ch ch-black start" aria-labelledby="start-h">
      <form
        className="blank"
        action="/paper"
        onSubmit={(e) => {
          e.preventDefault();
          window.location.href = `/paper?cash=${cash}`;
        }}
      >
        <h2 id="start-h" className="blank-h">
          <span className="blank-l1">
            내 AI와{" "}
            <span className="blank-inline">
              <label className="blank-field" data-ui>
                <span className="sr-only">처음 자금</span>
                <select value={cash} onChange={(e) => setCash(Number(e.target.value))} name="cash">
                  {CASH.map((c) => (
                    <option key={c} value={c}>
                      {cashLabel(c)}
                    </option>
                  ))}
                </select>
              </label>
              으로
            </span>
          </span>
          <span className="blank-l2">
            겨뤄{" "}
            <button type="submit" className="blank-btn">
              볼게요
            </button>
          </span>
        </h2>
        <label className="blank-phone" data-ui>
          <span>처음 자금</span>
          <select value={cash} onChange={(e) => setCash(Number(e.target.value))} aria-label="처음 자금">
            {CASH.map((c) => (
              <option key={c} value={c}>
                {cashLabel(c)}
              </option>
            ))}
          </select>
        </label>
      </form>
      <p className="start-note">
        누르면 카카오나 구글로 로그인한 뒤 같은 금액의 AI 계좌가 함께 열리고, 다음 거래일 09:05부터 수익률이 나란히
        기록됩니다.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------- 8. 블록으로 쌓은 이름
const MARK = [
  { l: "S", c: "sky", h: 100 },
  { l: "t", c: "pink", h: 74 },
  { l: "o", c: "yellow", h: 62 },
  { l: "c", c: "red", h: 80 },
  { l: "k", c: "blue", h: 92 },
  { l: "S", c: "sky", h: 100 },
  { l: "i", c: "yellow", h: 70 },
  { l: "m", c: "pink", h: 86 },
];

function Footer() {
  return (
    <footer className="ch-black foot" aria-label="StockSim">
      <div className="mark" role="img" aria-label="StockSim">
        {MARK.map((m, i) => (
          <span key={i} className={`mk-${m.c}`} style={{ height: `${m.h}%` }} aria-hidden>
            {m.l}
          </span>
        ))}
      </div>
      <div className="foot-cols">
        <nav aria-label="바로가기">
          <Link href="/predict">예측 순위</Link>
          <Link href="/paper">모의투자</Link>
          <Link href="/ranking">수익률 랭킹</Link>
          <Link href="/performance">모델 성적표</Link>
        </nav>
        <p className="foot-credits">
          사진과 영상: <a href="https://commons.wikimedia.org/wiki/File:Korea_exchange.JPG">“Korea exchange”</a>{" "}
          hyolee2, <a href="https://creativecommons.org/licenses/by-sa/3.0">CC BY-SA 3.0</a>;{" "}
          <a href="https://commons.wikimedia.org/wiki/File:View-of-Seoul-from63-Building-2019-7-27.webm">
            “View of Seoul from 63 Building”
          </a>{" "}
          Nesnad, <a href="https://creativecommons.org/licenses/by-sa/4.0">CC BY-SA 4.0</a>. 모두 Wikimedia Commons.
        </p>
      </div>
      <div className="foot-last">
        <span>© 2026 StockSim</span>
        <Link className="btn-y btn-y-sm" href="/predict">
          예측 순위 보기
        </Link>
      </div>
    </footer>
  );
}

export default function Home() {
  const stocks = useSWR<StockSummary[]>(["/stocks", null], swrFetcher);
  const list = stocks.data ?? [];
  return (
    <div className="home">
      <Hero stocks={stocks.data} error={!!stocks.error} />
      {list.length > 0 && <TypeAStock stocks={list} />}
      <DayBar />
      <PastCards stocks={list} />
      <Names stocks={list} />
      <Leaders />
      <Start />
      <Footer />
    </div>
  );
}
