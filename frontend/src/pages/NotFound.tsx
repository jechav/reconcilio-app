import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";

export function NotFound() {
  return (
    <div className="flex flex-col items-start gap-4 pt-12">
      <p className="text-sm font-semibold text-navy-600">404</p>
      <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
        Page not found
      </h1>
      <p className="max-w-md text-sm text-gray-500">
        The page you&apos;re looking for doesn&apos;t exist or was moved.
      </p>
      <Button asChild>
        <Link to="/dashboard">Go to dashboard</Link>
      </Button>
    </div>
  );
}
