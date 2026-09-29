import { ArrowLeftRight } from "lucide-react";
import type { ReactNode } from "react";

export function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen bg-white">
      <div className="hidden w-[560px] shrink-0 flex-col justify-between bg-navy-600 p-12 lg:flex">
        <div className="flex items-center gap-2.5">
          <span className="flex size-8 items-center justify-center rounded-[9px] bg-white text-navy-600">
            <ArrowLeftRight className="size-[18px]" aria-hidden />
          </span>
          <span className="text-xl font-bold tracking-tight text-white">Reconcilio</span>
        </div>
        <div className="flex flex-col gap-4">
          <p className="text-[40px] leading-[46px] font-bold tracking-tight text-white">
            Every expense, matched to its paper trail.
          </p>
          <p className="text-base leading-6 text-[#c9d8e6]">
            Upload receipts and bank statements. Reconcilio extracts, categorizes and reconciles
            them, so tax season starts organized.
          </p>
        </div>
        <p className="text-[13px] text-[#8fa9c2]">
          Your documents stay private to your organization.
        </p>
      </div>
      <main className="flex flex-1 items-center justify-center p-6">
        <div className="flex w-full max-w-[380px] flex-col gap-6">{children}</div>
      </main>
    </div>
  );
}
