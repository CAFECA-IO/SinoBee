# Security checklist for reviews

Check each item only where the changed code touches it; report only what you can trace to a concrete
attack input.

| Area | Look for | Safe pattern |
|---|---|---|
| SQL | string formatting / concatenation into queries | parameters (`cursor.execute(sql, params)`), ORM query builders |
| Shell | `shell=True`, `os.system`, backticks with user input | argument lists (`subprocess.run([...])`), `shlex.quote` |
| Paths | user input joined into file paths; `..`, absolute paths, symlinks | resolve and check the result stays under the allowed root |
| Templates / HTML | unescaped user data in HTML, `innerHTML`, `|safe`, `dangerouslySetInnerHTML` | auto-escaping, `textContent`, sanitiser for rich text |
| AuthZ | new endpoints or actions without a permission check; IDs taken from the request trusted | check the caller owns / may act on the object, server-side |
| AuthN / sessions | tokens in URLs or logs, missing expiry, comparing secrets with `==` | headers / cookies (HttpOnly, Secure, SameSite), `hmac.compare_digest` |
| Secrets | keys, passwords, tokens in code, tests, config, logs, error messages | environment / secret store; redact in logs |
| Deserialisation | `pickle`, `yaml.load`, `eval`, `exec`, Java/PHP native deserialisation of untrusted data | JSON, `yaml.safe_load`, explicit schemas |
| SSRF | fetching URLs the user supplies from the server | allow-list hosts, block private / link-local / metadata IPs after DNS resolution |
| Crypto | MD5/SHA1 for passwords, ECB mode, fixed IVs, `random` for tokens | bcrypt/scrypt/argon2, AES-GCM with random nonces, `secrets` |
| Web | permissive CORS with credentials, missing CSRF on state-changing requests, open redirects | explicit origins, CSRF tokens / SameSite, redirect allow-lists |
| Resource limits | unbounded uploads, regex on user input (ReDoS), unbounded loops / recursion | size limits, timeouts, linear-time patterns |
| Dependencies | new packages, unpinned versions, install scripts | pinned versions from known publishers |

Severity: **high** = exploitable by an outside attacker or leaks secrets/data; **medium** = needs an
authenticated or unusual position; **low** = defence in depth.
