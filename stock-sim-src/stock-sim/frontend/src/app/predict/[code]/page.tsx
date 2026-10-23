import PredictDetail from "@/components/predict/PredictDetail";

export default function Page({ params }: { params: { code: string } }) {
  return <PredictDetail code={params.code} />;
}
