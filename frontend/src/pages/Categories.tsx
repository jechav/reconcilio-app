import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Plus, Tag, X } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate } from "react-router-dom";
import { z } from "zod";

import {
  ApiError,
  createCategory,
  deleteCategory,
  listCategories,
  updateCategory,
  type CategoryOut,
} from "@/api/client";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { getSession } from "@/session";

const createdFormat = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const schema = z.object({
  name: z.string().trim().min(1, "Enter a category name.").max(255, "Use 255 characters or fewer."),
});

type FormValues = z.infer<typeof schema>;

type DialogState =
  | { kind: "create" }
  | { kind: "edit"; category: CategoryOut }
  | { kind: "delete"; category: CategoryOut }
  | null;

function messageFor(error: unknown, fallback: string): string | null {
  if (!error) return null;
  return error instanceof ApiError ? error.message : fallback;
}

function CategoryFormDialog({
  category,
  token,
  onClose,
}: {
  category?: CategoryOut;
  token: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const editing = category !== undefined;
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: category?.name ?? "" },
  });

  const save = useMutation({
    mutationFn: ({ name }: FormValues) =>
      editing ? updateCategory(token, category.id, name) : createCategory(token, name),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["categories"] });
      onClose();
    },
  });
  const apiError = messageFor(save.error, "Failed to save category.");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? "Rename category" : "Add category"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Renaming keeps every transaction in this category."
              : "Categories are labels you assign to transactions. Each transaction has one."}
          </DialogDescription>
        </DialogHeader>
        <form
          noValidate
          aria-label={editing ? "Rename category" : "Add category"}
          onSubmit={handleSubmit((values) => save.mutate(values))}
          className="flex flex-col gap-4"
        >
          <FormField
            id="category-name"
            label="Category name"
            placeholder="e.g. Professional services"
            autoFocus
            error={errors.name?.message}
            {...register("name")}
          />
          {apiError && (
            <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
              {apiError}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
              {editing ? "Save" : "Create category"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteCategoryDialog({
  category,
  token,
  onClose,
}: {
  category: CategoryOut;
  token: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: () => deleteCategory(token, category.id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["categories"] });
      onClose();
    },
  });
  const apiError = messageFor(remove.error, "Failed to delete category.");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete category</DialogTitle>
          <DialogDescription>
            Delete &ldquo;{category.name}&rdquo;? Its transactions are kept but become
            uncategorized. This can&apos;t be undone.
          </DialogDescription>
        </DialogHeader>
        {apiError && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
            {apiError}
          </p>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            {remove.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function Categories() {
  const session = getSession();
  const token = session?.access_token ?? "";
  const canManage = session?.role === "owner" || session?.role === "admin";

  const [dialog, setDialog] = useState<DialogState>(null);

  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: () => listCategories(token),
    enabled: session !== null,
  });

  if (!session) {
    return <Navigate to="/login" replace />;
  }

  const loadError = messageFor(categories.error, "Failed to load categories.");
  const list = categories.data;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-[28px] leading-[34px] font-bold tracking-tight text-gray-900">
            Categories
          </h1>
          <p className="text-sm text-gray-500">
            Manage the categories used to label your transactions
          </p>
        </div>
        {canManage && (
          <Button type="button" onClick={() => setDialog({ kind: "create" })} className="h-10">
            <Plus aria-hidden />
            Add category
          </Button>
        )}
      </header>

      {!canManage && (
        <p className="max-w-xl rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-700">
          Only an owner or admin can add, rename or delete categories.
        </p>
      )}

      {loadError && (
        <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-500">
          {loadError}
        </p>
      )}

      <section aria-label="Categories" className="flex flex-col gap-3">
        {list === undefined && !loadError && (
          <>
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-[74px] animate-pulse rounded-xl bg-gray-100" />
            ))}
          </>
        )}
        {list?.length === 0 && (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed bg-white px-6 py-12 text-center">
            <Tag className="size-6 text-gray-500" aria-hidden />
            <p className="font-semibold text-gray-900">No categories yet</p>
            <p className="text-sm text-gray-500">
              {canManage
                ? "Add your first category to start labeling transactions."
                : "An owner or admin can add categories."}
            </p>
          </div>
        )}
        {list?.map((category) => (
          <div
            key={category.id}
            className="flex items-center gap-4 rounded-xl border bg-white px-5 py-4"
          >
            <span
              aria-hidden
              className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-navy-50 text-navy-600"
            >
              <Tag className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-gray-900">{category.name}</p>
              <p className="text-xs text-gray-500">
                Created {createdFormat.format(new Date(category.created_at))}
              </p>
            </div>
            {canManage && (
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Rename ${category.name}`}
                  onClick={() => setDialog({ kind: "edit", category })}
                  className="size-10 bg-gray-50"
                >
                  <Pencil aria-hidden />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Delete ${category.name}`}
                  onClick={() => setDialog({ kind: "delete", category })}
                  className="size-10 bg-rose-50 text-rose-500 hover:bg-rose-100 hover:text-rose-500"
                >
                  <X aria-hidden />
                </Button>
              </div>
            )}
          </div>
        ))}
      </section>

      {dialog?.kind === "create" && (
        <CategoryFormDialog token={token} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "edit" && (
        <CategoryFormDialog
          key={dialog.category.id}
          category={dialog.category}
          token={token}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === "delete" && (
        <DeleteCategoryDialog
          category={dialog.category}
          token={token}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
