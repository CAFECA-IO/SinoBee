---
name: browser-form-filling
description: Logs in, fills and submits web forms and completes multi-step website flows with the browser tool, confirming before irreversible submissions. Use for sign-in, registration, booking, ordering or any web form (登入、表單、報名、預約、下單).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: browser, forms, login, web
  sinobee-triggers: 登入, 表單, 填寫, 報名, 預約, 下單, login, sign in, fill, form, book, submit
  sinobee-requires-tools: browser
  author: sinobee
---

# When to Use
Interacting with websites: logging in, filling and submitting forms, multi-page flows, dashboards.

# Procedure
1. `browser` navigate to the page; read the element list. Login state persists in the browser profile, so
   check whether you are already signed in before asking for credentials.
2. Fill fields with action=type using refs from the LATEST snapshot; use action=select for dropdowns;
   click checkboxes/radios by ref.
3. Before submitting anything with real-world effect (payment, booking, sending, publishing, deleting),
   summarise what will be submitted and get the user's confirmation unless they explicitly pre-approved it.
4. Submit (type with submit=true, or click the submit button). Read the resulting snapshot for success or
   validation errors; fix errors and retry.
5. If a captcha, 2FA code or unknown password is required, stop and ask the user (they can act in the
   live desktop view, then tell you to continue).

# Pitfalls
- Refs change after every navigation or DOM update - never reuse old refs.
- Elements marked (offscreen) may need scroll before clicking (click scrolls automatically, but verify).
- Some sites open results in a new tab: check action=tabs.
- Never type passwords that the user did not provide; never store them in memory.

# Verification
The final snapshot shows a confirmation message/page; report its key text to the user.
