# Security

Please report security problems privately through GitHub: **Security → Report a vulnerability** on this repository. Do not open a public issue for them.

## What this repo never contains
- API keys (Gemini, Tavily), database passwords or admin tokens. They exist only in the server's git-ignored `.env`, entered with `deploy/set-keys.sh`.
- Cloud project IDs, service-account keys or SSH keys.

If you deploy your own copy: copy `.env.example` to `.env` on your server, fill it in there, and never commit it.
