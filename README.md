# Article audio feeds

The Flask app serves account-specific RSS feeds:

- `/uusijuttu/front?username=...&password=...`
- `/longplay?username=...&password=...`

For Longplay, add the `/longplay` URL on your server to your podcast player,
using your Longplay account credentials. URL-encode the username and password,
including characters such as `+`, `&` and `#`. Use HTTPS and keep the resulting
URL and the generated feed private: they contain your credentials.

The Longplay feed discovers articles from <https://www.longplay.fi/feed.xml>,
signs in to Longplay, and resolves recordings using that account's user ID.
Only articles with audio accessible to the account are included. Article
titles, links, GUIDs and publication dates are retained. Descriptions use the
article's introductory paragraphs from the existing article-index API requests,
falling back to the original RSS description. Plain-text podcast summaries are
also included. This adds no individual article page requests. Cover images
become podcast artwork and recordings become MP3 enclosures. New
articles appear when the player refreshes the feed, within the upstream feed's
article window.

Longplay's CDN MP3 links contain an expiring signature (`Expires`, `KeyName`
and `Signature`). Each enclosure instead points to a stable URL on this app:
`/longplay/audio/<article-id>.mp3?username=...&password=...`. When the player
requests it, the app signs in again, checks current access through the private
files API and redirects with HTTP 302 to a freshly signed CDN URL. This also
works for HEAD requests. The CDN handles the audio download and byte ranges;
the app does not store audio files or CDN signatures. Playback therefore does
not depend on the signature obtained when the feed was generated.

Missing credentials return HTTP 400, rejected authentication returns 401,
unavailable audio returns 404, upstream failures return 502, and upstream
timeouts return 504. Longplay responses, including playback redirects, use
`Cache-Control: private, no-store` and `Referrer-Policy: no-referrer`.

Install dependencies and run the tests:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Tests mock Longplay's HTTP responses and do not require account credentials.
