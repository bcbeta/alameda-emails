# Website Architecture

This repository is a static website for browsing Alameda CORA email records. There is no server-side application and no build step. The browser loads `index.html`, fetches JSON files from the repo, and renders the archive directly in JavaScript.

## Main Files

- `index.html` contains the page structure, styling, filtering UI, and all client-side rendering logic.
- `alameda_emails.json` is the primary email database used by the browser.
- `withheld_documents.json` is a small manifest for standalone withheld files that should appear outside the normal email-card data, such as the Alameda briefing PowerPoint.
- `scripts/import_withheld_documents.py` imports newly released withheld material into the static assets and JSON data.
- `Alameda CORA pdf files/` contains the PDFs, attachments, screenshots, and withheld-document assets hosted by the site.

## Runtime Flow

1. A static file server serves this repository, usually from the repo root.
2. The browser opens `index.html`.
3. `index.html` fetches `alameda_emails.json` and `withheld_documents.json`.
4. JavaScript in `index.html` renders:
   - the main searchable email archive,
   - the highlighted-email filter,
   - date and month navigation,
   - screenshot/document sections,
   - the withheld-documents section.

Because the page uses `fetch()` for local JSON files, it should be opened through an HTTP server rather than directly from `file://`.

## Data Model

Email records in `alameda_emails.json` use compact keys to keep the file small:

- `s`: subject
- `n`: sender name
- `e`: sender email
- `to`: recipients
- `cc`: copied recipients
- `d`: date string
- `b`: body text or extracted text
- `f`: PDF filename or repo-relative PDF path
- `pid`: Google Drive file id for older public email PDFs
- `att`: attachments
- `h`: whether the email belongs in the highlighted filter
- `x`: search text used by the UI
- `c`: category, currently used for withheld records with `withheld`
- `g`: withheld-document group name

Older public emails mostly use Google Drive ids in `pid`. New withheld records are hosted directly in the repository and use repo-relative paths in `f` and `att[].path`.

`withheld_documents.json` is only for standalone files that are not email cards. The withheld emails themselves are rendered from `alameda_emails.json`.

## Static Assets

The main asset areas are:

- `Alameda CORA pdf files/relevant_email_pdfs/` for existing email PDFs.
- `Alameda CORA pdf files/Attachments/` for existing public-record attachments.
- `Alameda CORA pdf files/withheld_documents/` for generated withheld PDFs and extracted attachments.
- Root-level support PDFs such as `Certification Log.pdf` and `Deliberative process certification.pdf`.

The withheld importer regenerates `Alameda CORA pdf files/withheld_documents/`, so manual edits inside that folder may be overwritten.

## Withheld Document Import

The source material currently lives outside this repo at:

```text
/Users/davidmintzer/Documents/GitHub/Withheld documents
```

Run the importer from the repo root:

```bash
python3 scripts/import_withheld_documents.py
```

The script:

1. reads `.msg` files from the withheld source folders,
2. converts those emails to hosted PDFs,
3. extracts `.msg` attachments into the repo,
4. turns selected briefing PDFs into email-style card records,
5. copies standalone files such as PowerPoint decks,
6. updates `alameda_emails.json` and `withheld_documents.json`.

The importer depends on Python packages for `.msg` parsing and PDF generation, plus `pdftotext` for extracting text from briefing PDFs.

## Local Development

From the repo root:

```bash
python3 -m http.server 8001
```

Then open:

```text
http://127.0.0.1:8001/
```

Useful checks after changing data or rendering code:

```bash
python3 -m json.tool alameda_emails.json >/dev/null
python3 -m json.tool withheld_documents.json >/dev/null
```

For JavaScript changes, also refresh the local page and check the browser console.

## Deployment

The site is designed to be deployed as static files, such as through GitHub Pages. Updating the live site is a matter of committing and pushing changes to the HTML, JSON, scripts, and hosted assets.
