import Link from "next/link";
import { resolveLocale } from "@/app/lib/i18n/locale";
import { getDictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./document.module.css";

interface Props {
  readonly params: Promise<{ org: string; id: string }>;
}

export default async function DocumentPage({ params }: Props) {
  const { org, id } = await params;
  const dict = getDictionary(await resolveLocale());

  return (
    <main className={styles.wrap}>
      <Link className={styles.back} href={`/${org}/search`}>
        ← {dict.search.label}
      </Link>
      <h1 className={styles.title}>{dict.appName}</h1>
      <p className={styles.id}>{id}</p>
    </main>
  );
}
