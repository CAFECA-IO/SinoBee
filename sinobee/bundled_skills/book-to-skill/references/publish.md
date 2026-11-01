# Sharing a generated skill (optional, Step 11)

From upstream book-to-skill, adapted for SinoBee. Offer this once, only after the Step 9.5 scan passed, and only
if the user wants to share the skill.

## Copyright gate — always first

The chapter files are synthesized summaries, but they still derive from the source. Skills generated from
**third-party copyrighted books stay private**. Sharing publicly is allowed only when the source is the user's
own writing, openly licensed content, or material the user explicitly confirms they may redistribute — say which
case applies. Access to internal company material is not permission to disclose it.

## Visibility is a separate one-word question

Ask: "Private or public? Reply with one word: `private` or `public`." Only the bare word `public` means public.
A sentence about the source ("it's public domain", "it's publicly available") describes the material, not the
destination, and means private. If the reply is not the bare word after one re-ask, use private and say so.

## Where to share

- **Export a file** (works everywhere): `sinobee skills export <skill_name>` writes `<skill_name>-<version>.tar.gz`
  that another SinoBee installs with `sinobee skills install ./<file>`.
- **SinoBee Skill Hub** (`sinobee skills publish <skill_name>`, needs the user's Hub key): Hub listings are
  public, so this is only for material that passed the copyright gate as public.
- **GitHub**, when the user asks for it and `gh` is installed and authenticated (`gh auth status`):
  1. Export the skill (above) and unpack it into a scratch folder in the workspace — never `git init` inside the
     SinoBee skills folder.
  2. Add a `README.md` there: title, one paragraph ("Agent skill generated from *<Title>* by <Author> with
     [book-to-skill](https://github.com/virgiliojr94/book-to-skill)"), the file list, and a note that the
     content is synthesized summaries, not the book text.
  3. `git init -b main && git add -A && git commit -m "Add <skill_name> skill"` then
     `gh repo create <repo_name> --private --source . --push` — `--public` only under the visibility rule above.
  4. Report the repo URL and the install command `npx skills add https://github.com/<owner>/<repo_name> --skill <skill_name>`.
