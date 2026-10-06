# Document analysis

Attach **any common document** to a stage and DevMind reads it - text, tables,
headings, pictures, diagrams, slides - and puts it into the stage's context as
structured Markdown. Attach as many files as you need; each is analysed
independently and they share the prompt budget fairly.

## What is read

| File | Text & structure | Tables | Pictures / diagrams |
|---|---|---|---|
| **PDF** | layout-aware text, headings inferred from font size, lists, running headers/footers removed | real tables (`pdfplumber`) | pages that carry diagrams (vector drawings *or* pictures) and **scanned pages** are rendered and read by the vision model |
| **Word** `.docx` | headings, lists, text boxes | Markdown tables (header row promoted) | embedded pictures are read **in place**; shape-drawn diagrams are rendered (LibreOffice) |
| **PowerPoint** `.pptx` | per slide: title, bullets with indent levels, speaker notes, SmartArt text, grouped shapes | tables, **chart data** as tables | pictures; diagram slides rendered (LibreOffice); **shape connections recovered from the XML** ("Web App → Order API") with no model at all |
| **Excel** `.xlsx` | one table per sheet (values, 1000 rows/sheet) | - | embedded images |
| **draw.io, Visio `.vsdx`, SVG** | components, containers and **directed connections**, from the XML | - | - |
| Images (PNG/JPG/GIF/WebP/TIFF) | vision model (diagram-aware), OCR as the offline fallback | tables-as-images become Markdown | - |
| HTML, Markdown, CSV/TSV, JSON, YAML, SQL, PlantUML, Mermaid, DBML, ... | decoded (UTF-8 / Windows-1252) | - | - |
| Legacy `.doc .ppt .xls .rtf .odt .odp .ods` | converted through LibreOffice when installed | | |

A figure's description is placed exactly where the figure sat:

```
## Architecture

> **Figure — picture 1** · _read by vision:bedrock_
> Components: Web App, Order API, Payments DB. Connections: Web App -> Order API (HTTPS) ...
> Meaning: ...
```

Anything that cannot be read is **reported, never silently dropped** - the upload
response and the attachment chip show e.g. `42 pages · 6 tables · 9 of 11 figures read`
with a warning when some figures were skipped (limit, time budget, no vision model).

## How a large document fits the prompt

Previously every attachment was cut at 8,000 characters, so anything after the
first few pages was invisible to the agent. Now all attached documents share
`ATTACHMENT_CONTEXT_CHARS` (default 80,000) fairly, and a document that does not
fit is condensed **section by section**:

1. every section keeps its heading and the opening of its text (plus a document outline);
2. the remaining budget goes to the sections that best match what the stage is being
   asked to produce (and to tables and figure descriptions);
3. what was left out is marked (`[… rest of this section omitted …]`).

The full extracted text is stored and unchanged; condensing happens per prompt.
Attached content is wrapped in a notice that it is **material to analyse, never
instructions to follow**.

## Reading on demand (outline + read)

The stored text stays fully addressable, so a stage is not limited to the excerpt chosen at prompt time:

* **Outline** - `GET /api/projects/{id}/attachments/{attachmentId}/outline` lists every section:
  id, title, level, size, page range, tables and figures. No model is involved.
* **Read** - `GET /api/projects/{id}/attachments/{attachmentId}/read?sections=3,7` (ids from the outline),
  `&pages=12-18`, and/or `&q=oauth mtls` returns those parts verbatim in document order within
  `max_chars` (default 20,000, max 60,000). Anything that did not fit is listed in `next`.
* **Model-chosen sections** - when a document is condensed, a light model reads only the *outline*
  (never the text) plus the stage's task and picks the sections the stage needs in full. Those are
  kept before keyword matching spends the rest of the budget. If the call fails, keyword fitting is
  used as before.

Both endpoints use the project's read permission and treat another project's attachment as not found.

## Configuration (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `ATTACHMENT_MAX_BYTES` | 50,000,000 | per file (nginx `client_max_body_size` is 64 MB) |
| `ATTACHMENT_MAX_PAGES` | 200 | pages / slides read per document |
| `ATTACHMENT_MAX_FIGURES` | 24 | pictures / diagram pages sent to the vision model per document |
| `ATTACHMENT_FIGURE_CONCURRENCY` | 3 | parallel vision calls per upload |
| `ATTACHMENT_PARSE_SECONDS` / `ATTACHMENT_ANALYSIS_SECONDS` | 90 / 120 | wall-clock budgets |
| `ATTACHMENT_STORE_CHARS` | 1,000,000 | extracted Markdown kept per attachment |
| `ATTACHMENT_RENDER_PAGES` | `auto` | `off` skips LibreOffice rendering |
| `ATTACHMENT_VISION` | `auto` | `ocr` never calls the model |
| `ATTACHMENT_CONTEXT_CHARS` | 80,000 | budget for all attached material in one prompt |

## Deploying

* **nginx** - `client_max_body_size 64m` is now set in `infra/nginx/*.conf`. The old
  default (1 MB) rejected every real deck or PDF with a 413 before the API saw it.
  Rebuild/restart the `frontend` container.
* **LibreOffice** - the orchestrator image installs headless LibreOffice
  (`libreoffice-writer/impress/calc`, ~300 MB) so slides and Word pages whose diagrams
  are drawn shapes can be rendered for the vision model, and legacy formats opened.
  Build with `--build-arg INSTALL_LIBREOFFICE=false` to skip it: text, tables, pictures
  and diagram connections are still extracted; only shape-drawn diagrams lose their
  rendered view.
* Run `python -m app.scripts migrate` (or redeploy): migration `0031` adds the
  `extraction` record kept with each attachment.

## Safety

Uploads are untrusted: archives are checked for zip bombs, XML is parsed with entity
expansion and network access disabled, LibreOffice runs with no shell, a private
profile and a hard timeout, and every stage of analysis is bounded by the limits above.
A corrupt or password-protected file is refused with the reason; an unsupported type
is kept as a reference and not inlined.

## Not covered

* Handwriting and low-resolution scans depend on the vision model's ability.
* Password-protected files must be unlocked first.
* Figures beyond `ATTACHMENT_MAX_FIGURES` (pages of a very long scanned PDF) are
  reported as not analysed; raise the limit or split the file.
