import "server-only";
import { cookies, headers } from "next/headers";
import { DEFAULT_LOCALE, LOCALES, type Locale } from "./dictionaries";

export const LOCALE_COOKIE = "locale";

function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value);
}

// Cookie wins (an explicit choice); otherwise the first Accept-Language tag
// the app supports; otherwise the default.
export async function resolveLocale(): Promise<Locale> {
  const cookie = (await cookies()).get(LOCALE_COOKIE)?.value;
  if (cookie && isLocale(cookie)) {
    return cookie;
  }
  const accept = (await headers()).get("accept-language") ?? "";
  for (const part of accept.split(",")) {
    const tag = part.split(";")[0]?.trim().slice(0, 2).toLowerCase() ?? "";
    if (isLocale(tag)) {
      return tag;
    }
  }
  return DEFAULT_LOCALE;
}
