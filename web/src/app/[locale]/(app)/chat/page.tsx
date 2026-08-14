import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { ChatClient } from "@/app/[locale]/(app)/chat/chat-client";

/** Chat page (`/chat`). RSC shell; all interactivity (SSE turn, thread list, composer) lives in the
 * `ChatClient` leaf. The app shell (sidebar/top bar/rail + auth guard) is provided by the `(app)`
 * group layout. */
export default async function ChatPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <ChatClient />;
}
