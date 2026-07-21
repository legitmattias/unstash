"use client";

import { useTransition } from "react";
import { setLocale } from "@/app/lib/i18n/actions";
import { LOCALES, type Locale } from "@/app/lib/i18n/dictionaries";
import styles from "./Header.module.css";

interface Props {
  readonly locale: Locale;
  readonly label: string;
}

export function LanguageSwitcher({ locale, label }: Props) {
  const [pending, startTransition] = useTransition();

  return (
    <div className={styles.langs} role="group" aria-label={label}>
      {LOCALES.map((option) => (
        <button
          key={option}
          type="button"
          className={styles.lang}
          aria-pressed={option === locale}
          disabled={pending}
          onClick={() =>
            startTransition(async () => {
              await setLocale(option);
            })
          }
        >
          {option.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
