import Link from "next/link";
import { redirect } from "next/navigation";
import type { CSSProperties } from "react";
import { apiFetch, type ClusterDocument, type ClustersResponse } from "@/app/lib/api";
import { categoryBadge } from "@/app/lib/doctype";
import { resolveLocale } from "@/app/lib/i18n/locale";
import { getDictionary } from "@/app/lib/i18n/dictionaries";
import { Header } from "@/app/components/Header";
import { ReclusterButton } from "./ReclusterButton";
import styles from "./categories.module.css";

interface PageProps {
  readonly params: Promise<{ org: string }>;
}

export default async function CategoriesPage({ params }: PageProps) {
  const { org } = await params;
  const locale = await resolveLocale();
  const dict = getDictionary(locale);

  const res = await apiFetch(`/api/orgs/${org}/clusters`);
  if (res.status === 401 || res.status === 403) {
    redirect(`/login?next=${encodeURIComponent(`/${org}/categories`)}`);
  }
  const data = (await res.json()) as ClustersResponse;

  const members = new Map<string, readonly ClusterDocument[]>();
  await Promise.all(
    data.clusters.map(async (cluster) => {
      const docsRes = await apiFetch(`/api/orgs/${org}/clusters/${cluster.id}/documents`);
      members.set(cluster.id, docsRes.ok ? ((await docsRes.json()) as ClusterDocument[]) : []);
    }),
  );

  return (
    <div className={styles.page}>
      <Header org={org} dict={dict} locale={locale} />
      <main className={styles.main}>
        <div className={styles.headrow}>
          <h1 className={styles.title}>{dict.categories.title}</h1>
          <ReclusterButton org={org} dict={dict.categories} />
        </div>
        {data.clusters.length === 0 ? (
          <p className={styles.empty}>{dict.categories.empty}</p>
        ) : (
          <ul className={styles.list}>
            {data.clusters.map((cluster) => {
              const badge = categoryBadge(cluster.label);
              return (
                <li
                  key={cluster.id}
                  className={styles.card}
                  style={{ "--tab": badge.colorVar } as CSSProperties}
                >
                  <span className={styles.label}>{cluster.label}</span>
                  <span className={styles.keywords}>
                    {cluster.keywords
                      .slice(0, 6)
                      .map((keyword) => keyword.term)
                      .join(" · ")}
                  </span>
                  <details className={styles.members}>
                    <summary className={styles.membersSummary}>
                      {dict.categories.documentsCount.replace("{count}", String(cluster.size))}
                    </summary>
                    <ul className={styles.memberList}>
                      {(members.get(cluster.id) ?? []).map((doc) => (
                        <li key={doc.id}>
                          <Link
                            className={
                              cluster.representative_document_ids.includes(doc.id)
                                ? styles.memberRepresentative
                                : styles.memberLink
                            }
                            href={`/${org}/documents/${doc.id}`}
                          >
                            {doc.title}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </details>
                </li>
              );
            })}
          </ul>
        )}
      </main>
    </div>
  );
}
