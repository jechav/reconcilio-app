import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";

import { signup } from "@/api/client";
import { AuthLayout } from "@/components/AuthLayout";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { useAuthSubmit } from "@/useAuthSubmit";

const schema = z.object({
  orgName: z.string().trim().min(1, "Enter your organization's name.").max(255),
  email: z.string().min(1, "Enter your email.").email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
});

type SignupValues = z.infer<typeof schema>;

export function Signup() {
  const { error, submitting, run } = useAuthSubmit();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<SignupValues>({ resolver: zodResolver(schema) });

  return (
    <AuthLayout>
      <div className="flex flex-col gap-1.5">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Create your organization
        </h1>
        <p className="text-sm text-gray-500">
          Start reconciling in a few minutes. You'll be the owner.
        </p>
      </div>

      <form
        aria-label="Sign up"
        noValidate
        onSubmit={handleSubmit((values) =>
          run(() => signup(values.email, values.password, values.orgName)),
        )}
        className="flex flex-col gap-6"
      >
        <div className="flex flex-col gap-4">
          <FormField
            id="signup-org"
            label="Organization name"
            autoComplete="organization"
            error={errors.orgName?.message}
            {...register("orgName")}
          />
          <FormField
            id="signup-email"
            label="Work email"
            type="email"
            autoComplete="email"
            error={errors.email?.message}
            {...register("email")}
          />
          <FormField
            id="signup-password"
            label="Password"
            type="password"
            autoComplete="new-password"
            hint="At least 8 characters."
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
          {submitting ? "Creating…" : "Create organization"}
        </Button>
      </form>

      <p className="text-center text-sm text-gray-500">
        Already have an account?{" "}
        <Link to="/login" className="font-semibold text-navy-600 hover:underline">
          Log in
        </Link>
      </p>
    </AuthLayout>
  );
}
