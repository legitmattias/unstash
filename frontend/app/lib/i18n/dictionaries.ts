export type Locale = "sv" | "en";

export const LOCALES: readonly Locale[] = ["sv", "en"];
export const DEFAULT_LOCALE: Locale = "sv";

// Plain strings only — the dictionary is passed into client components, so
// it must cross the server/client boundary as serialisable data. Counts and
// query echoes use "{name}" placeholders, substituted at the call site.
export interface Dictionary {
  readonly appName: string;
  readonly common: {
    readonly signOut: string;
    readonly language: string;
    readonly theme: string;
    readonly themeLight: string;
    readonly themeDark: string;
  };
  readonly search: {
    readonly placeholder: string;
    readonly submit: string;
    readonly label: string;
    readonly resultsCount: string;
    readonly emptyTitle: string;
    readonly emptyBody: string;
    readonly zeroTitle: string;
    readonly zeroBody: string;
    readonly errorTitle: string;
    readonly errorBody: string;
  };
  readonly filters: {
    readonly heading: string;
    readonly date: string;
    readonly from: string;
    readonly to: string;
    readonly type: string;
    readonly allTypes: string;
    readonly category: string;
    readonly allCategories: string;
    readonly clear: string;
  };
  readonly categories: {
    readonly title: string;
    readonly empty: string;
    readonly documentsCount: string;
    readonly recluster: string;
    readonly requested: string;
    readonly inProgress: string;
    readonly failed: string;
  };
  readonly login: {
    readonly title: string;
    readonly subtitle: string;
    readonly email: string;
    readonly password: string;
    readonly submit: string;
    readonly error: string;
  };
  readonly orgs: {
    readonly title: string;
    readonly subtitle: string;
  };
}

const sv: Dictionary = {
  appName: "Unstash",
  common: {
    signOut: "Logga ut",
    language: "Språk",
    theme: "Tema",
    themeLight: "Ljust",
    themeDark: "Mörkt",
  },
  search: {
    placeholder: "Sök i föreningens dokument…",
    submit: "Sök",
    label: "Sök",
    resultsCount: "{count} träffar",
    emptyTitle: "Sök i arkivet",
    emptyBody: "Skriv en fråga för att hitta protokoll, avtal, ekonomi och information.",
    zeroTitle: "Inga träffar för ”{query}”",
    zeroBody: "Prova andra ord, eller ta bort ett filter.",
    errorTitle: "Sökningen kunde inte slutföras",
    errorBody: "Försök igen om en stund.",
  },
  filters: {
    heading: "Filter",
    date: "Datum",
    from: "Från",
    to: "Till",
    type: "Typ",
    allTypes: "Alla typer",
    category: "Kategori",
    allCategories: "Alla kategorier",
    clear: "Rensa filter",
  },
  categories: {
    title: "Kategorier",
    empty: "Inga kategorier ännu — de skapas automatiskt när arkivet växer.",
    documentsCount: "{count} dokument",
    recluster: "Uppdatera kategorier",
    requested: "Uppdatering startad. Kategorierna byggs om i bakgrunden.",
    inProgress: "En uppdatering pågår redan.",
    failed: "Kunde inte starta uppdateringen.",
  },
  login: {
    title: "Logga in",
    subtitle: "Ange dina uppgifter för att fortsätta.",
    email: "E-post",
    password: "Lösenord",
    submit: "Logga in",
    error: "Fel e-post eller lösenord.",
  },
  orgs: {
    title: "Välj förening",
    subtitle: "Du har tillgång till flera föreningar.",
  },
};

const en: Dictionary = {
  appName: "Unstash",
  common: {
    signOut: "Sign out",
    language: "Language",
    theme: "Theme",
    themeLight: "Light",
    themeDark: "Dark",
  },
  search: {
    placeholder: "Search your documents…",
    submit: "Search",
    label: "Search",
    resultsCount: "{count} results",
    emptyTitle: "Search the archive",
    emptyBody: "Type a question to find minutes, contracts, finances, and notices.",
    zeroTitle: "No results for “{query}”",
    zeroBody: "Try different words, or clear a filter.",
    errorTitle: "Search could not be completed",
    errorBody: "Please try again in a moment.",
  },
  filters: {
    heading: "Filters",
    date: "Date",
    from: "From",
    to: "To",
    type: "Type",
    allTypes: "All types",
    category: "Category",
    allCategories: "All categories",
    clear: "Clear filters",
  },
  categories: {
    title: "Categories",
    empty: "No categories yet — they are created automatically as the archive grows.",
    documentsCount: "{count} documents",
    recluster: "Refresh categories",
    requested: "Refresh started. Categories are rebuilt in the background.",
    inProgress: "A refresh is already in progress.",
    failed: "Could not start the refresh.",
  },
  login: {
    title: "Sign in",
    subtitle: "Enter your details to continue.",
    email: "Email",
    password: "Password",
    submit: "Sign in",
    error: "Wrong email or password.",
  },
  orgs: {
    title: "Choose an organisation",
    subtitle: "You have access to more than one.",
  },
};

const DICTIONARIES: Record<Locale, Dictionary> = { sv, en };

export function getDictionary(locale: Locale): Dictionary {
  return DICTIONARIES[locale];
}
