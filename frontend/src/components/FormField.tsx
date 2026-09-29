import { forwardRef, type ComponentProps } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface FormFieldProps extends ComponentProps<typeof Input> {
  id: string;
  label: string;
  error?: string;
  hint?: string;
}

export const FormField = forwardRef<HTMLInputElement, FormFieldProps>(function FormField(
  { id, label, error, hint, ...inputProps },
  ref,
) {
  const messageId = `${id}-message`;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} className="text-[13px] font-semibold text-gray-700">
        {label}
      </Label>
      <Input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? messageId : undefined}
        ref={ref}
        className="h-10"
        {...inputProps}
      />
      {error ? (
        <p id={messageId} className="text-xs text-rose-500">
          {error}
        </p>
      ) : hint ? (
        <p id={messageId} className="text-xs text-gray-500">
          {hint}
        </p>
      ) : null}
    </div>
  );
});
