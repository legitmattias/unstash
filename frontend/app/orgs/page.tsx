import Link from "next/link";
import { redirect } from "next/navigation";
import { getMe, getMyOrgs } from "@/app/lib/session";
import { resolveLocale } from "@/app/lib/i18n/locale";
import { getDictionary } from "@/app/lib/i18n/dictionaries";
import styles from "./orgs.module.css";

export default async function OrgsPage() {
  const me = await getMe();
  if (!me) {
    redirect("/login");
  }
  const orgs = await getMyOrgs();
  if (orgs.length === 1) {
    redirect(`/${orgs[0].slug}/search`);
  }
  const dict = getDictionary(await resolveLocale());

  return (
    <main className={styles.wrap}>
      <div className={styles.panel}>
        <span className={styles.brand}>{dict.appName}</span>
        <h1 className={styles.title}>{dict.orgs.title}</h1>
        <p className={styles.subtitle}>{dict.orgs.subtitle}</p>
        <ul className={styles.list}>
          {orgs.map((org) => (
            <li key={org.slug}>
              <Link className={styles.org} href={`/${org.slug}/search`}>
                <span className={styles.name}>{org.name}</span>
                <span className={styles.role}>{org.role}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </main>
  );
}
