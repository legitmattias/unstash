// Filing colour and label for a result. The learned category label wins
// when the org has been clustered; the file-type mapping is the fallback
// for unclustered orgs and noise documents.
export interface DocType {
  readonly label: string;
  readonly colorVar: string;
}

const CATEGORY_COLORS: readonly string[] = [
  "var(--type-protokoll)",
  "var(--type-avtal)",
  "var(--type-ekonomi)",
  "var(--type-info)",
];

// The colour is derived from the label so a category keeps its colour
// across renders and re-clusters that preserve the label.
export function categoryBadge(label: string): DocType {
  let hash = 0;
  for (const char of label) {
    hash = (hash * 31 + char.codePointAt(0)!) >>> 0;
  }
  return { label, colorVar: CATEGORY_COLORS[hash % CATEGORY_COLORS.length] };
}

export function docBadge(mimeType: string, categoryLabel: string | null): DocType {
  return categoryLabel ? categoryBadge(categoryLabel) : docType(mimeType);
}

const BY_MIME: ReadonlyMap<string, DocType> = new Map([
  ["application/pdf", { label: "PDF", colorVar: "var(--type-protokoll)" }],
  [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    { label: "Word", colorVar: "var(--type-avtal)" },
  ],
  ["text/markdown", { label: "Text", colorVar: "var(--type-ekonomi)" }],
  ["text/plain", { label: "Text", colorVar: "var(--type-ekonomi)" }],
]);

const FALLBACK: DocType = { label: "Fil", colorVar: "var(--muted)" };

export function docType(mimeType: string): DocType {
  const exact = BY_MIME.get(mimeType);
  if (exact) {
    return exact;
  }
  if (mimeType.startsWith("image/")) {
    return { label: "Bild", colorVar: "var(--type-info)" };
  }
  return FALLBACK;
}
