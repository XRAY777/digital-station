# Security

Passwords use Argon2id hashes. Access/refresh tokens are opaque, stored hashed in the database, issued in HttpOnly SameSite cookies and marked Secure on HTTPS. Logout revokes the session; refresh rotation rejects reuse/expiry. Login failures are limited and audited without passwords or tokens.

The backend enforces ADMIN, DISPATCHER and OBSERVER permissions on every protected route. Mutations require an allowed Origin and a CSRF token; WebSocket upgrades validate Origin and session. The frontend hides unavailable actions for usability, but its checks are not authoritative. Production should serve credentials over TLS and restrict LAN/server ingress to trusted clients.

Pydantic validates request bodies; ORM/parameterized SQL is used for database access. Critical commands recheck live safety, reserve resources transactionally and support `Idempotency-Key` where implemented. AI output is untrusted: it can only reference current server variants, and ACCEPT repeats version/feasibility checks. API responses use restrictive cache and browser security headers. Audit records security-sensitive decisions without secrets.

SQLite local demo is intended for one host/process. The Docker profile exposes only nginx; PostgreSQL and the backend stay on the Compose network. Set credentials through environment variables; `.env.example` contains placeholders only.
