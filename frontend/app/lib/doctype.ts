// Filing colour and label for a result, keyed on the file type.
// Classification (M5) will replace this mapping with the learned
// document category.
export interface DocType {
  readonly label: string;
  readonly colorVar: string;
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
