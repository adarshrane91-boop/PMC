# PMC Civic Signal - updated backend

- `MockSource` is opt-in only (`CIVIC_INCLUDE_MOCK=1`).
- Added `PublicSocialSource` for public PMC Facebook/Instagram post URLs in `data/pmc_social_posts.json`.
- No PMC Facebook/Instagram credentials are required for this source.
- Ward/place hints are used before text matching.
- Public post URL/account URL are preserved in API reports.
- Frontend popups show the official source name and an `Open original post` link.
- Reddit/Mastodon are disabled in the launcher.
- Google News RSS remains enabled.

**Limitation:** an official account URL alone does not grant permission to download every post. This implementation intentionally does not scrape Facebook/Instagram. Fully automatic official-account ingestion requires a Meta-supported API/access route or compliant third-party provider.

## Manifest
```json
{
  "accounts": {
    "facebook": {"name": "PMC Official Facebook", "url": "OFFICIAL_ACCOUNT_URL"},
    "instagram": {"name": "PMC Official Instagram", "url": "OFFICIAL_ACCOUNT_URL"}
  },
  "posts": [
    {
      "platform": "facebook",
      "post_url": "PUBLIC_POST_URL",
      "account_url": "OFFICIAL_ACCOUNT_URL",
      "text": "PMC road repair work started in Kothrud",
      "posted_at": "2026-09-30T10:00:00+05:30",
      "ward": "Kothrud"
    }
  ]
}
```
