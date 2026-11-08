# Security

Please report vulnerabilities privately through [GitHub security advisories](https://github.com/pablomanjarres/big-brother-eye/security/advisories/new), not in public issues. You should get a reply within a week.

## What to know

* The database is a plain SQLite file in your user profile. Anyone who can read your user files can read your activity history. Use full-disk encryption.
* `secrets.env` holds API keys. `bbeye init` creates it readable only by you on macOS and Linux.
* The HTTP MCP server listens on `127.0.0.1` by default and rejects requests without `BBEYE_MCP_TOKEN` or with an unexpected `Host` header. If you expose it through a tunnel, the token is the only thing protecting it: use a long random value and rotate it if it leaks.
* Model files are downloaded from pinned URLs and checked against SHA-256 hashes before use.
