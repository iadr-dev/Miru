import { type RenderResult, render } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement } from "react";
import messages from "../../messages/zh-TW.json";

/** Render a component the way the app does: inside next-intl with the real zh-TW messages, so a
 * component test reads the same copy a user sees (and a missing key fails the test). */
export function renderWithIntl(ui: ReactElement): RenderResult {
  return render(
    <NextIntlClientProvider locale="zh-TW" messages={messages} timeZone="Asia/Taipei">
      {ui}
    </NextIntlClientProvider>,
  );
}

export { messages };
