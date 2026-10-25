import Home from "@/components/home/Home";

export const metadata = {
  title: "StockSim | 매일 장 마감 뒤 30종목 예측 순위와 AI 모의투자",
  description:
    "코스피 대형주 30종목의 5거래일 예측을 보고, 같은 돈으로 시작한 내 AI와 모의투자 수익률을 겨루는 교육용 서비스",
};

export default function HomePage() {
  return <Home />;
}
