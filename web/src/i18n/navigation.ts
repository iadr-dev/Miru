/** Locale-aware navigation primitives — use these instead of `next/link` / `next/navigation`
 * so every link/redirect carries the active locale prefix automatically. */
import { createNavigation } from "next-intl/navigation";
import { routing } from "@/i18n/routing";

export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
