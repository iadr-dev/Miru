"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useId, useState } from "react";
import { ConfirmDialog } from "@/components/settings/confirm-dialog";
import { FieldError, TextInput } from "@/components/settings/form-controls";
import { SettingsSection } from "@/components/settings/settings-section";
import { Button } from "@/components/ui/button";
import { DELETE_ACCOUNT_CONFIRMATION, deleteAccount } from "@/lib/api/auth";

/** The danger zone: permanently delete the account. Needs the confirmation word typed, because it
 * cancels billing and erases everything and cannot be undone. */
export function DeleteAccount(): ReactNode {
  const t = useTranslations("settings.profile.delete");
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();

  const close = (): void => {
    if (pending) return;
    setOpen(false);
    setTyped("");
    setError(null);
  };

  const confirm = async (): Promise<void> => {
    setPending(true);
    setError(null);
    const result = await deleteAccount();
    if (result.ok) {
      queryClient.clear();
      // A full navigation: nothing from the deleted session should survive in memory.
      window.location.assign("/");
      return;
    }
    setPending(false);
    setError(result.code === "billing_cancel_failed" ? t("errorBilling") : t("errorGeneric"));
  };

  return (
    <>
      <SettingsSection title={t("title")} description={t("description")}>
        <div className="pt-4">
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            {t("action")}
          </Button>
        </div>
      </SettingsSection>
      <ConfirmDialog
        open={open}
        title={t("dialogTitle")}
        description={t("dialogDescription")}
        confirmLabel={pending ? t("deleting") : t("confirm")}
        cancelLabel={t("cancel")}
        confirmDisabled={pending || typed !== DELETE_ACCOUNT_CONFIRMATION}
        onConfirm={() => void confirm()}
        onCancel={close}
      >
        <label htmlFor={inputId} className="block text-[13px] text-[var(--text-muted)]">
          {t("typePrompt", { word: DELETE_ACCOUNT_CONFIRMATION })}
        </label>
        <TextInput
          id={inputId}
          className="mt-2"
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          autoComplete="off"
        />
        {error ? (
          <div className="pt-3">
            <FieldError message={error} />
          </div>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
