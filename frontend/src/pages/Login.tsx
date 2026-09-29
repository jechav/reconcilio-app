import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";

import { login } from "@/api/client";
import { AuthLayout } from "@/components/AuthLayout";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { useAuthSubmit } from "@/useAuthSubmit";

const schema = z.object({
  email: z.string().min(1, "Enter your email.").email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

type LoginValues = z.infer<typeof schema>;

export function Login() {
  const { error, submitting, run } = useAuthSubmit();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginValues>({ resolver: zodResolver(schema) });

  return (
    <AuthLayout>
      <div className="flex flex-col gap-1.5">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Welcome back
        </h1>
        <p className="text-sm text-gray-500">Log in to your organization.</p>
      </div>

      <form
        aria-label="Log in"
        noValidate
        onSubmit={handleSubmit((values) => run(() => login(values.email, values.password)))}
        className="flex flex-col gap-6"
      >
        <div className="flex flex-col gap-4">
          <FormField
            id="login-email"
            label="Email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <FormField
            id="login-password"
            label="Password"
            type="password"
            autoComplete="current-password"
            error={errors.password?.message}
            {...register("password")}
          />
        </div>

        {error && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
            {error}
          </p>
        )}

        <Button type="submit" disabled={submitting} className="h-11 w-full">
          {submitting && <Loader2 className="animate-spin" aria-hidden />}
          {submitting ? "Logging in…" : "Log in"}
        </Button>
      </form>

      <p className="text-center text-sm text-gray-500">
        New to Reconcilio?{" "}
        <Link to="/signup" className="font-semibold text-navy-600 hover:underline">
          Create an organization
        </Link>
      </p>
    </AuthLayout>
  );
}
