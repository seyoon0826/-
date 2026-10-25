import { Suspense } from "react";

import PaperView from "@/components/paper/PaperView";
import { Skeleton } from "@/components/ui/States";

export const metadata = { title: "모의투자" };

export default function Page() {
  return (
    <Suspense fallback={<Skeleton lines={8} />}>
      <PaperView />
    </Suspense>
  );
}
