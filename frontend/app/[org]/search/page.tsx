import { redirect } from "next/navigation";
import { apiFetch, type SearchResponse } from "@/app/lib/api";
import { resolveLocale } from "@/app/lib/i18n/locale";
import { getDictionary } from "@/app/lib/i18n/dictionaries";
import { Header } from "@/app/components/Header";
import { SearchBox } from "./SearchBox";
import { Filters } from "./Filters";
import { Results } from "./Results";
import styles from "./search.module.css";

interface PageProps {
  readonly params: Promise<{ org: string }>;
  readonly searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

export default async function SearchPage({ params, searchParams }: PageProps) {
  const { org } = await params;
  const sp = await searchParams;
  const query = first(sp.q).trim();
  const mimeType = first(sp.mime_type);
  const dateFrom = first(sp.date_from);
  const dateTo = first(sp.date_to);
  const locale = await resolveLocale();
  const dict = getDictionary(locale);

  let data: SearchResponse | null = null;
  let failed = false;
  if (query) {
    const qs = new URLSearchParams({ q: query });
    if (mimeType) qs.set("mime_type", mimeType);
    if (dateFrom) qs.set("date_from", dateFrom);
    if (dateTo) qs.set("date_to", dateTo);
    const res = await apiFetch(`/api/orgs/${org}/search?${qs.toString()}`);
    if (res.status === 401 || res.status === 403) {
      redirect(`/login?next=${encodeURIComponent(`/${org}/search`)}`);
    }
    if (res.ok) {
      data = (await res.json()) as SearchResponse;
    } else {
      failed = true;
    }
  }

  return (
    <div className={styles.page}>
      <Header org={org} dict={dict} locale={locale} />
      <div className={styles.searchbar}>
        <SearchBox org={org} initialQuery={query} dict={dict.search} />
      </div>
      <div className={styles.layout}>
        <Filters
          org={org}
          mimeType={mimeType}
          dateFrom={dateFrom}
          dateTo={dateTo}
          dict={dict.filters}
        />
        <main className={styles.main}>
          <Results org={org} query={query} data={data} failed={failed} dict={dict.search} />
        </main>
      </div>
    </div>
  );
}
