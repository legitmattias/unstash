import { resolveLocale } from "@/app/lib/i18n/locale";
import { getDictionary } from "@/app/lib/i18n/dictionaries";
import { safeRedirectPath } from "@/app/lib/redirect";
import { LoginForm } from "./LoginForm";

interface Props {
  readonly searchParams: Promise<{ next?: string }>;
}

export default async function LoginPage({ searchParams }: Props) {
  const dict = getDictionary(await resolveLocale());
  const { next } = await searchParams;
  return <LoginForm dict={dict.login} appName={dict.appName} next={safeRedirectPath(next)} />;
}
