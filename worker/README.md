# Smriti Groq proxy

This Cloudflare Worker keeps the Groq API key out of the browser and Android APK.

Before deployment, install Wrangler, copy `wrangler.toml.example` to `wrangler.toml`, then add both secrets interactively:

```bash
wrangler secret put GROQ_API_KEY
wrangler secret put NILA_APP_TOKEN
wrangler deploy
```

The current token is appropriate for private testing, not a public multi-user release. Before Play Store publication, replace it with user authentication and device attestation.
