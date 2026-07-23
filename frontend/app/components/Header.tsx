import Link from "next/link";
import type { Dictionary, Locale } from "@/app/lib/i18n/dictionaries";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { ThemeToggle } from "./ThemeToggle";
import { SignOutButton } from "./SignOutButton";
import styles from "./Header.module.css";

interface Props {
  readonly org: string;
  readonly dict: Dictionary;
  readonly locale: Locale;
}

export function Header({ org, dict, locale }: Props) {
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link className={styles.brand} href="/">
          {dict.appName}
        </Link>
        <span className={styles.org}>{org}</span>
        <nav className={styles.nav} aria-label={dict.appName}>
          <Link className={styles.navLink} href={`/${org}/search`}>
            {dict.search.label}
          </Link>
          <Link className={styles.navLink} href={`/${org}/categories`}>
            {dict.categories.title}
          </Link>
        </nav>
        <div className={styles.actions}>
          <LanguageSwitcher locale={locale} label={dict.common.language} />
          <ThemeToggle
            label={dict.common.theme}
            lightLabel={dict.common.themeLight}
            darkLabel={dict.common.themeDark}
          />
          <SignOutButton label={dict.common.signOut} />
        </div>
      </div>
    </header>
  );
}
