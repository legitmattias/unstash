# ADR 0008: Gotenberg sidecar for legacy office → PDF conversion

## Status

Accepted.

## Context

The ingestion pipeline's strategy router classifies each uploaded document into one of four handling strategies. Modern formats (PDF, plain text, Markdown, HTML, and the OOXML family — `.docx`, `.xlsx`, `.pptx`) are read directly by Docling and routed to `EXTRACT`. Legacy binary office formats — `.doc`, `.xls`, `.ppt`, `.rtf`, and the OpenDocument family (`.odt`, `.ods`, `.odp`) — are not parseable by Docling and are routed to `CONVERT_THEN_EXTRACT`: they must be converted to PDF before the normal extraction path can run.

These legacy formats are a real, if minority, share of accumulated document archives, so they cannot simply be refused. The question is *how* to convert them.

The conversion engine of record for these formats is LibreOffice (headless). There are two ways to make it available to the worker:

1. **Bake LibreOffice into the worker image** and invoke it as a subprocess.
2. **Run a purpose-built conversion service as a sidecar** and call it over the internal network.

Two properties make the second option clearly preferable:

- **Isolation of an untrusted-input attack surface.** LibreOffice parsing arbitrary uploaded documents is a historically exploited attack surface (document-parser CVEs, macro handling). The worker process holds database credentials and runs inside the per-request/per-job tenant context; it is the last process that should be executing a full office suite on hostile bytes. A separate container confines that blast radius.
- **Image and lifecycle cost.** LibreOffice pulls in a large dependency tree. The worker image already carries Docling, transformers, and torch; adding a full office suite bloats it and slows every build and deploy. A dedicated, well-maintained upstream image also solves LibreOffice process lifecycle, per-conversion timeouts, and restart-after-N-conversions — operational problems we would otherwise reinvent.

## Decision

Run **Gotenberg** as a sidecar service in the compose stack and have the ingestion worker convert legacy office documents to PDF by calling it over the internal Docker network.

- **Image:** `gotenberg/gotenberg:8.34.0-libreoffice` — the **LibreOffice-only variant**, pinned. It omits the Chromium/HTML-rendering stack we do not use and is materially smaller than the full image. The tag is pinned because the bundled LibreOffice moves quickly.
- **Route:** the worker POSTs the source file to `/forms/libreoffice/convert` (multipart field `files`) and receives the converted PDF, which is then handed to the same Docling PDF extraction path as native PDFs.
- **Network posture:** the sidecar is attached to the internal network only, with **no published ports**. The conversion API executes LibreOffice on uploaded bytes and must never be reachable from outside the compose network.
- **Resource limits:** memory and CPU caps in the staging and production overrides. LibreOffice spins up per conversion and is otherwise idle, so the caps exist to contain a runaway conversion on a shared host rather than to reserve capacity.
- **Health and ordering:** a compose healthcheck against Gotenberg's `/health` endpoint; the worker `depends_on` the sidecar being healthy.
- **Provenance:** the converted PDF is written alongside the original so a future re-parse does not have to reconvert. The document's pipeline metadata records that it was `converted_via` Gotenberg and preserves the detected source MIME type.

Failures (sidecar unavailable, unconvertible document, timeout) surface through the pipeline's normal exception path: the document transitions to `failed` with the conversion error captured in its `parsing_error`, and the worker continues processing other jobs.

## Consequences

- **One additional service** in every environment's compose stack and one more image to pull. Idle resource cost is low; active cost is bounded by the configured limits.
- **Added latency for legacy formats:** an extra network hop plus LibreOffice runtime. Acceptable given these formats are a minority and the alternative is not supporting them.
- **Extension-driven conversion:** Gotenberg selects the LibreOffice import filter from the uploaded file's extension. Conversion therefore relies on the stored filename carrying a sensible extension. A file whose content is a legacy office format but whose name lacks the matching extension may fail conversion; this surfaces as a `failed` document with an actionable error rather than a silent miss.
- **Residual security risk:** a crafted document could target a LibreOffice vulnerability. This is contained by running the conversion in a separate, network-restricted, resource-capped container that holds no application secrets. Further hardening (read-only root filesystem, seccomp profile, egress restriction on the sidecar) is available if the threat model tightens, and is deliberately deferred.
- **Disk:** the converted PDF is retained beside the original, adding storage per converted document in exchange for cheaper re-parsing.

## Alternatives considered

- **Bake LibreOffice into the worker image and shell out to it.** Rejected: weaker isolation of the untrusted-input surface, significant image bloat on an already-heavy worker image, and the burden of managing LibreOffice lifecycle and timeouts ourselves.
- **The full Gotenberg image (with Chromium).** Rejected: we do not render HTML to PDF; the LibreOffice-only variant is smaller and has a narrower surface.
- **Refuse legacy formats.** Rejected: real archives contain `.doc`/`.xls`/`.ppt` and OpenDocument files; refusing them would leave a visible gap in ingestion coverage.
