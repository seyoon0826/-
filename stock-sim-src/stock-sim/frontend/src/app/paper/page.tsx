import { Suspense } from "react";

import PaperView from "@/components/paper/PaperView";
import { Skeleton } from "@/components/ui/States";

export default function Page() {
  return (
    <Suspense fallback={<Skeleton lines={8} />}>
      <PaperView />
    </Suspense>
  );
}
