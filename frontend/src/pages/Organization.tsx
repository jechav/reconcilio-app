import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import {
  ApiError,
  getLlmUsage,
  inviteMember,
  listMembers,
  updateOrgSettings,
  type OrgRole,
} from "@/api/client";
import { FormField } from "@/components/FormField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { getSession, saveSession } from "@/session";

const DEFAULT_THRESHOLD = 0.8;

const ROLE_LABELS: Record<OrgRole, string> = {
  owner: "Owner",
  admin: "Admin",
  member: "Member",
};

const ROLE_HINTS: Record<"admin" | "member", string> = {
  member: "Can upload, categorize and view everything.",
  admin: "Everything a member can do, plus manage categories and view the audit log.",
};

const inviteSchema = z.object({
  email: z.string().min(1, "Enter an email address.").email("Enter a valid email address."),
});

function messageFor(error: unknown, fallback: string): string | null {
  if (!error) return null;
  return error instanceof ApiError ? error.message : fallback;
}

function initials(email: string): string {
  return email.slice(0, 2).toUpperCase();
}

function InviteDialog({ token, onClose }: { token: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [role, setRole] = useState<"admin" | "member">("member");
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<{ email: string }>({ resolver: zodResolver(inviteSchema) });

  const invite = useMutation({
    mutationFn: ({ email }: { email: string }) => inviteMember(token, email, role),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["org", "members"] }),
  });
  const apiError = messageFor(invite.error, "Failed to invite member.");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        {invite.isSuccess ? (
          <>
            <DialogHeader>
              <DialogTitle>Member added</DialogTitle>
              <DialogDescription>
                {invite.data.user.email} was added as {ROLE_LABELS[invite.data.role]}. No email is
                sent yet: ask them to open <span className="font-mono">/accept-invite</span> and set
                a password with this address.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button type="button" onClick={onClose}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Invite member</DialogTitle>
              <DialogDescription>Add someone to your organization.</DialogDescription>
            </DialogHeader>
            <form
              noValidate
              aria-label="Invite member"
              onSubmit={handleSubmit((values) => invite.mutate(values))}
              className="flex flex-col gap-4"
            >
              <FormField
                id="invite-email"
                label="Email address"
                type="email"
                placeholder="member@example.com"
                autoFocus
                error={errors.email?.message}
                {...register("email")}
              />
              <div className="flex flex-col gap-2">
                <span id="invite-role-label" className="text-[13px] font-semibold text-gray-700">
                  Role
                </span>
                <div
                  role="radiogroup"
                  aria-labelledby="invite-role-label"
                  className="flex gap-1 rounded-lg bg-gray-100 p-1"
                >
                  {(["member", "admin"] as const).map((option) => (
                    <button
                      key={option}
                      type="button"
                      role="radio"
                      aria-checked={role === option}
                      onClick={() => setRole(option)}
                      className={cn(
                        "flex-1 rounded-md px-3 py-2 text-sm text-gray-500 transition-colors",
                        role === option && "bg-white font-semibold text-navy-600 shadow-sm",
                      )}
                    >
                      {ROLE_LABELS[option]}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-gray-500">{ROLE_HINTS[role]}</p>
              </div>
              {apiError && (
                <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
                  {apiError}
                </p>
              )}
              <DialogFooter>
                <Button type="button" variant="outline" onClick={onClose}>
                  Cancel
                </Button>
                <Button type="submit" disabled={invite.isPending}>
                  {invite.isPending && <Loader2 className="animate-spin" aria-hidden />}
                  Add member
                </Button>
              </DialogFooter>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function Organization() {
  const session = getSession();
  const token = session?.access_token ?? "";
  const isOwner = session?.role === "owner";
  const enabled = session !== null;

  const savedThreshold = Number(session?.organization.confidence_threshold ?? DEFAULT_THRESHOLD);
  const [threshold, setThreshold] = useState(savedThreshold);
  const [inviting, setInviting] = useState(false);

  const members = useQuery({
    queryKey: ["org", "members"],
    queryFn: () => listMembers(token),
    enabled,
  });
  const usage = useQuery({
    queryKey: ["org", "llm-usage"],
    queryFn: () => getLlmUsage(token),
    enabled,
  });

  const saveSettings = useMutation({
    mutationFn: (value: number) => updateOrgSettings(token, value),
    onSuccess: (organization) => {
      if (session)
        saveSession({ ...session, organization: { ...session.organization, ...organization } });
      setThreshold(Number(organization.confidence_threshold));
      toast.success("Extraction threshold saved");
    },
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  const dirty = Math.abs(threshold - savedThreshold) > 0.0001;
  const settingsError = messageFor(saveSettings.error, "Failed to save settings.");
  const loadError =
    messageFor(members.error, "Failed to load members.") ??
    messageFor(usage.error, "Failed to load AI usage.");

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
          Organization
        </h1>
        <p className="text-sm text-gray-500">
          {session.organization.name} · Manage extraction settings, members and AI usage
        </p>
      </header>

      {loadError && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {loadError}
        </p>
      )}

      <section
        aria-label="Extraction settings"
        className="flex flex-col gap-6 rounded-xl border bg-white p-6 lg:flex-row lg:items-center lg:gap-12"
      >
        <div className="flex flex-1 flex-col gap-1.5">
          <h2 className="text-base font-bold text-gray-900">Extraction confidence threshold</h2>
          <p className="text-sm leading-5 text-gray-500">
            Fields the extractor is less sure about than this are re-checked by the language model.
            Higher is more accurate but uses more AI calls.
          </p>
        </div>
        <div className="flex w-full flex-col gap-3 lg:w-96">
          <div className="flex items-baseline justify-between text-xs text-gray-500">
            <span>Fewer AI calls</span>
            <span aria-live="polite" className="text-[22px] font-bold text-navy-600 tabular-nums">
              {threshold.toFixed(2)}
            </span>
            <span>More accurate</span>
          </div>
          <input
            type="range"
            aria-label="Confidence threshold"
            min={0.05}
            max={1}
            step={0.05}
            value={threshold}
            disabled={!isOwner}
            onChange={(event) => setThreshold(Number(event.target.value))}
            className="h-2 w-full cursor-pointer accent-navy-600 disabled:cursor-not-allowed disabled:opacity-60"
          />
          {settingsError && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
              {settingsError}
            </p>
          )}
          {isOwner ? (
            <div className="flex justify-end">
              <Button
                type="button"
                disabled={!dirty || saveSettings.isPending}
                onClick={() => saveSettings.mutate(Number(threshold.toFixed(2)))}
              >
                {saveSettings.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden />
                ) : (
                  !dirty && saveSettings.isSuccess && <Check aria-hidden />
                )}
                Save
              </Button>
            </div>
          ) : (
            <p className="text-xs text-gray-500">Only the owner can change this.</p>
          )}
        </div>
      </section>

      <div className="flex flex-col items-start gap-6 xl:flex-row">
        <section aria-label="Members" className="w-full rounded-xl border bg-white xl:flex-[1.4]">
          <div className="flex items-center justify-between px-6 py-[18px]">
            <h2 className="text-base font-bold text-gray-900">Members</h2>
            {isOwner && (
              <Button type="button" onClick={() => setInviting(true)}>
                <Plus aria-hidden />
                Invite member
              </Button>
            )}
          </div>
          {members.data === undefined && !members.error && (
            <div className="flex flex-col gap-3 px-6 pb-6">
              {[0, 1].map((i) => (
                <div key={i} className="h-8 animate-pulse rounded-md bg-gray-100" />
              ))}
            </div>
          )}
          <ul>
            {members.data?.map((membership) => (
              <li
                key={membership.id}
                className="flex items-center gap-3 border-t border-gray-100 px-6 py-3.5"
              >
                <span
                  aria-hidden
                  className={cn(
                    "flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-bold",
                    membership.role === "owner"
                      ? "bg-navy-100 text-navy-600"
                      : "bg-gray-100 text-gray-700",
                  )}
                >
                  {initials(membership.user.email)}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-gray-900">
                  {membership.user.email}
                </span>
                <Badge
                  variant="secondary"
                  className={cn(
                    "rounded-full",
                    membership.role === "owner" && "bg-navy-50 text-navy-600",
                  )}
                >
                  {ROLE_LABELS[membership.role]}
                </Badge>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="AI usage" className="w-full rounded-xl border bg-white xl:flex-1">
          <div className="flex flex-col gap-0.5 px-6 pt-[18px] pb-3.5">
            <h2 className="text-base font-bold text-gray-900">AI usage</h2>
            <p className="text-[13px] text-gray-500">Total calls per model</p>
          </div>
          {usage.data === undefined && !usage.error && (
            <div className="px-6 pb-6">
              <div className="h-8 animate-pulse rounded-md bg-gray-100" />
            </div>
          )}
          {usage.data?.length === 0 && (
            <p className="border-t px-6 py-4 text-sm text-gray-500">No AI calls yet.</p>
          )}
          {usage.data && usage.data.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-y bg-gray-50 text-left text-xs font-semibold tracking-wide text-gray-500 uppercase">
                  <th className="px-6 py-2 font-semibold">Provider · Model</th>
                  <th className="px-6 py-2 text-right font-semibold">Calls</th>
                </tr>
              </thead>
              <tbody>
                {usage.data.map((row) => (
                  <tr key={`${row.provider}/${row.model}`} className="border-b last:border-b-0">
                    <td className="px-6 py-3.5">
                      <p className="font-semibold text-gray-900">{row.provider}</p>
                      <p className="text-xs text-gray-500">{row.model}</p>
                    </td>
                    <td className="px-6 py-3.5 text-right tabular-nums text-gray-900">
                      {row.calls.toLocaleString("en-US")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      {inviting && <InviteDialog token={token} onClose={() => setInviting(false)} />}
    </div>
  );
}
