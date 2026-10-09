"use client";

import { PawPrintIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useCallback, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { getPetProgress, listPetCatalog, pullPet } from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import { petName, RARITY_RING, RARITY_TEXT, shouldOfferWelcomePull } from "@/lib/pets";
import { useFocusTrap } from "@/lib/use-focus-trap";
import type { PullOutcome } from "@/types/settings";

const DISMISSED_KEY = "miru.pet.welcome-dismissed";

function wasDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

function rememberDismissed(): void {
  try {
    window.localStorage.setItem(DISMISSED_KEY, "1");
  } catch {
    // Private mode: the offer simply shows again next visit.
  }
}

/** The free first pull: a new user with no pet and a pull waiting is offered it once. Closing
 * without pulling keeps the pull for later (the pets page, or the nudge beside the composer) and
 * is remembered, so the dialog never nags. Cosmetic only - nothing here touches answers or quota. */
export function WelcomePullDialog(): ReactNode {
  const t = useTranslations("settings.pets");
  const tw = useTranslations("settings.pets.welcome");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const reduceMotion = useReducedMotion();
  const dialogRef = useRef<HTMLDivElement>(null);
  const [dismissed, setDismissed] = useState(wasDismissed);
  const [outcome, setOutcome] = useState<PullOutcome | null>(null);
  const [failed, setFailed] = useState(false);

  const progress = useQuery({ queryKey: ["settings", "pet-progress"], queryFn: getPetProgress });
  const catalog = useQuery({
    queryKey: ["settings", "pet-catalog"],
    queryFn: listPetCatalog,
    staleTime: 60 * 60_000,
  });

  const pull = useMutation({
    mutationFn: pullPet,
    onSuccess: (result) => {
      setOutcome(result);
      for (const key of ["pet-progress", "pet-active", "pets-owned"]) {
        void queryClient.invalidateQueries({ queryKey: ["settings", key] });
      }
    },
    onError: () => setFailed(true),
  });

  const open = outcome !== null || (!dismissed && shouldOfferWelcomePull(progress.data));

  const close = useCallback(() => {
    if (outcome === null) {
      rememberDismissed();
    }
    setDismissed(true);
    setOutcome(null);
  }, [outcome]);

  const confirm = useCallback(() => {
    if (outcome !== null) {
      close();
    } else if (!pull.isPending) {
      setFailed(false);
      pull.mutate();
    }
  }, [outcome, close, pull]);

  useFocusTrap(dialogRef, open, close, confirm);

  const entry = outcome ? catalog.data?.find((e) => e.pet_id === outcome.pet_id) : undefined;

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reduceMotion ? 0 : 0.15 }}
        >
          <button
            type="button"
            aria-label={tw("later")}
            className="absolute inset-0 bg-black/60"
            onClick={close}
            tabIndex={-1}
          />
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="welcome-pull-title"
            className="relative w-full max-w-sm rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-6 text-center shadow-2xl"
          >
            {outcome ? (
              <Reveal
                outcome={outcome}
                name={petName(catalog.data ?? [], outcome.pet_id, locale)}
                rarityLabel={t(`rarity.${outcome.rarity}`)}
                description={locale === "zh-TW" ? null : (entry?.description ?? null)}
                reduceMotion={reduceMotion === true}
                onDone={close}
              />
            ) : (
              <>
                <span className="mx-auto flex size-16 items-center justify-center rounded-full border-2 border-[var(--border)] bg-[var(--surface-2)]">
                  <PawPrintIcon size={30} weight="regular" aria-hidden />
                </span>
                <h2
                  id="welcome-pull-title"
                  className="mt-4 font-semibold text-[var(--text)] text-lg"
                >
                  {tw("title")}
                </h2>
                <p className="mt-2 text-[var(--text-muted)] text-sm">{tw("body")}</p>
                {failed ? (
                  <p role="alert" className="mt-2 text-[12px] text-[var(--warn)]">
                    {tw("error")}
                  </p>
                ) : null}
                <div className="mt-5 flex justify-center gap-2">
                  <Button variant="ghost" size="sm" onClick={close}>
                    {tw("later")}
                  </Button>
                  <Button variant="filled" size="sm" disabled={pull.isPending} onClick={confirm}>
                    {pull.isPending ? tw("pulling") : tw("pull")}
                  </Button>
                </div>
              </>
            )}
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function Reveal({
  outcome,
  name,
  rarityLabel,
  description,
  reduceMotion,
  onDone,
}: {
  outcome: PullOutcome;
  name: string;
  rarityLabel: string;
  description: string | null;
  reduceMotion: boolean;
  onDone: () => void;
}): ReactNode {
  const tw = useTranslations("settings.pets.welcome");
  return (
    <>
      <motion.span
        className={cn(
          "mx-auto flex size-20 items-center justify-center rounded-full border-2 bg-[var(--surface-2)]",
          RARITY_RING[outcome.rarity],
        )}
        initial={reduceMotion ? false : { scale: 0.6, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={
          reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 200, damping: 16 }
        }
      >
        <PawPrintIcon
          size={36}
          weight="regular"
          className={RARITY_TEXT[outcome.rarity]}
          aria-hidden
        />
      </motion.span>
      <h2 id="welcome-pull-title" className="mt-4 font-semibold text-[var(--text)] text-lg">
        {tw("revealTitle", { name })}
      </h2>
      <p className={cn("mt-1 text-sm", RARITY_TEXT[outcome.rarity])}>{rarityLabel}</p>
      {description ? <p className="mt-2 text-[var(--text-muted)] text-sm">{description}</p> : null}
      <p className="mt-3 text-[12px] text-[var(--text-muted)]">
        {outcome.is_duplicate ? tw("duplicate", { xp: outcome.xp_awarded }) : tw("equipped")}
      </p>
      <div className="mt-5 flex justify-center">
        <Button variant="filled" size="sm" onClick={onDone}>
          {tw("start")}
        </Button>
      </div>
    </>
  );
}
