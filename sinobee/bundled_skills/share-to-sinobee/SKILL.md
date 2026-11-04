---
name: share-to-sinobee
description: Uploads files a task made (a report, a page, pictures, data, documents, a folder) to sinobee.ai and shares them - first checks every file for security risks (programs, macros, PDF scripts, phishing or mining pages, unsafe archives, disguised files, credential files, secrets, personal data, photo locations), cleans what can be cleaned into copies, then asks the user to confirm and returns the link, public ones listed at sinobee.ai/shares. Use when the user asks to upload, share, publish or post files to sinobee.ai (分享到 sinobee.ai、上傳到 SinoBee.ai、公開分享、發布到 shares、給我分享連結).
license: MIT
metadata:
  version: 1.1.0
  author: sinobee
  sinobee-tags: share, upload, publish, security, privacy, sinobee.ai
  sinobee-triggers: 分享到 sinobee.ai, 上傳到 sinobee.ai, 分享到 SinoBee.ai, 分享連結, 公開分享, 發布, 上傳分享, sinobee.ai/shares, share on sinobee.ai, upload to sinobee.ai, share link, publish
  sinobee-requires-tools: share_check, share_files
  sinobee-entry: scripts/run.py
  sinobee-quickstart: "1) list what to share (only the deliverables, never data/raw/ downloads or notes); 2) share_check(paths); 3) ⛔ found -> leave the file out, or skill_run(share-to-sinobee, \"clean <file> <outdir>\") for a clean copy, then share_check the copies; 4) ⚠️ found -> tell the user in one line each; 5) share_files(paths, title, description, visibility, expires_days) - SinoBee asks the user who may see it and for how long; 6) answer with the link and what was masked."
---

# When to Use
The user wants files on sinobee.ai: "把報告分享到 sinobee.ai", "上傳這些圖給我連結", "公開到 shares",
"publish this page". Also when a task's request ends with sharing ("做完後分享"), as its last step.

Not for: sharing the whole conversation (the user does that with the 分享 button), sending files by
e-mail or to another service, or backing files up.

# How sharing works here
- `share_check(paths)` looks at the real files on the user's computer and uploads nothing.
- `share_files(paths, ...)` checks them again, then asks the user two questions: who may see it -
  **公開** (anyone, listed at sinobee.ai/shares) or **只給知道連結的人** - with the files and the
  findings, and then how long - **7 天**, **30 天** or **不過期** (until revoked). It uploads only after
  both are answered.
- Nothing with a ⛔ finding is ever uploaded, and neither tool can be talked out of it. Do not try to get a
  file past the check (renaming it, zipping it, pasting it into another file, base64): that is the very
  thing the check exists for. Fix the file or leave it out.
- No user to ask (a scheduled or unattended task) = no upload. Say the files are ready and that the user
  can share them from the result card.
- The user must be signed in to sinobee.ai (設定 → SinoBee.ai 帳號). If not, say so and stop.

# Procedure
1. **Choose the files.** Share the deliverables: the report, the page and what it loads, the charts, the
   final data. Leave out raw downloads (`data/raw/`, saved web pages, PDFs fetched as sources), notes,
   scratch, tests, build scripts, `.env` and anything the user did not mean to publish. If unsure, ask.
   - One report or page (`.md`, `.html`, `.svg`): pass that one file. The pictures, styles and scripts it
     uses go with it automatically.
   - Several files or a folder: pass them. SinoBee adds an index page (index.md) that links each one.
2. **Check.** Run `share_check(paths=[...], title=..., description=...)`. Read every finding.
3. **⛔ (block) - fix or drop, never upload as is.**

   | Finding | What to do |
   |---|---|
   | executable, macro, archive_executable | Leave it out. Share the source or a PDF/screenshot instead. Never rename it. |
   | pdf_javascript / pdf_launch / pdf_uri | Make a clean PDF: export it again from its source (pdf-documents skill). `clean` does not remove scripts. |
   | credential_harvest, cryptominer | Leave it out and tell the user. A page you made should never contain these; if it does, the page is compromised. |
   | zip_traversal / zip_symlink / zip_bomb / nested archive | Share the files themselves, not the archive; or rebuild a plain `.zip` |
   | type_mismatch, polyglot | The file is not what its name says. Leave it out and tell the user. |
   | credential_file (.env, keys, .pem …) | Leave it out. Never share it. |
   | stored_secret / api_key / token / password / private_key in a PDF, Office file or archive | That text cannot be masked: rebuild the file from its source without the secret. Or `clean` an Office file, which reports the secret but cannot remove it from body text. |
   | too_large / too_many | Share fewer files, or compress images and videos (ai-video-production / data-visualization) |
4. **⚠️ (warn) - the user decides.** Tell the user each warning in one short line. Remove what is easy to
   remove:
   - **gps** (photo location): `skill_run(share-to-sinobee, "clean <photo> <outdir>")` writes a copy
     without EXIF. Share the copy.
   - **metadata** (author, company): `clean` removes Office and PDF document properties.
   - **Secrets in text files** (.md .html .csv .json …) are masked (••••) on upload automatically. Still,
     prefer removing them from the source.
   - **Personal data** (身分證字號, 卡號, 手機, e-mail): ask whether to remove or mask them. Do not share
     other people's personal data publicly without the user saying so. SinoBee suggests 只給知道連結的人
     when it sees personal data.
   - **external_script / send_external / external_frame / redirect**: a page that loads code or sends data
     elsewhere. Remove it if you added it. Pages you build should be self-contained (frontend-design).
   - **prompt_injection / bidi**: hidden direction characters or text that instructs an AI. Remove or
     explain them.
5. **Re-check the copies.** Run `share_check` on exactly the files you will share, until there is no ⛔.
6. **Share.** Call `share_files(paths=[...], title="…", description="…", visibility="PUBLIC" or "LINK",
   expires_days=30)`.
   - Title: what it is, at most 30 Chinese characters, without tool or skill names.
   - Description: one sentence on what the reader will see.
   - Suggest `LINK` for anything personal or internal, and a duration that fits: `expires_days=7` for a
     draft or something time-bound, 30 for most, 0 (never) only for something meant to stay published.
     The user makes both final choices in the questions; do not ask them again in the chat.
7. **Answer.** Give the link, who can see it (public: listed at sinobee.ai/shares), how long the user chose
   (the date it expires, or 不過期), what
   was masked, and the warnings the user accepted. Mention that it can be revoked at sinobee.ai/shares or
   from SinoBee's share list. If the user chose 不要分享, say nothing was uploaded.

# The clean script
`skill_run(share-to-sinobee, "<command> ...")`. It runs in the sandbox, writes new files and never
changes the originals. Output is JSON.

| Command | Does |
|---|---|
| `clean <file> <outdir>` | A copy without metadata: JPEG/PNG/WebP without EXIF/GPS/text chunks; .docx/.xlsx/.pptx without author, company, comments' authors and custom properties (macros and embedded objects removed); PDF re-written without document info (needs pypdf) |
| `inventory <folder>` | Lists the files of a folder with size, type and a suggestion (share / leave out: raw download, scratch, credential, build script) to help with step 1 |

`clean` only removes metadata and macros. It does not remove secrets or personal data from the body
text; edit the source for that.

# Gotchas
- A ⛔ that you think is a false alarm (for example a security course's sample phishing page): leave the
  file out and explain to the user. They can share it themselves from SinoBee's 分享 dialog after
  reviewing it. Do not work around the check.
- `share_check` runs on the real files. Its result is authoritative; your own reading of a file is not.
- Text files are masked on upload, but PDFs, Office files, images and archives are uploaded byte for byte.
  A secret inside them is a ⛔.
- A public share is visible to anyone and may be indexed. Treat 公開 as publishing.
- Do not share the same files twice. To change a share, the user edits or revokes it at
  sinobee.ai/shares.
