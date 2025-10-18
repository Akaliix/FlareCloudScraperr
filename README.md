# FlareCloudScraperr

Combine Cloudscraper and FlareSolverr to handle Cloudflare-protected requests efficiently.

This project provides a small utility to:

- Expose an authenticated SOCKS5 proxy as a local HTTP proxy (bridge).
- Use FlareSolverr to obtain Cloudflare cookies and user-agent by routing requests through that local HTTP proxy.
- Initialize a Cloudscraper session that uses the bridged HTTP proxy and the cookies returned by FlareSolverr.
- Refresh cookies automatically when Cloudscraper receives a 403 Forbidden response.

Why this exists
---------------
Cloudscraper is fast but can be blocked by Cloudflare after repeated requests. FlareSolverr is slower but reliably bypasses Cloudflare challenges. By using FlareSolverr to obtain valid cookies and feeding them into Cloudscraper while routing traffic through a local HTTP proxy bound to a SOCKS5 upstream, you can get both performance and reliability.

Quick features
--------------

- Supports SOCKS5 upstream proxies with authentication by bridging to a local HTTP proxy.
- Easily editable to use HTTP proxies (with or without auth) or SOCKS proxies without auth.
- Example code in `scraper_factory.py`, `proxy_bridge.py`, and `flaresolverr_client.py`.

Requirements
------------

- Python 3.8+
- See `requirements.txt` for exact dependencies. Install with:

```powershell
pip install -r requirements.txt
```

Usage
-----

1. Configure a proxy string. Example formats:

- Authenticated SOCKS5 (used by this project):

```
socks5://username:password@1.2.3.4:1080
```

2. Edit `test.py` or run the included example. By default `test.py` will read the `SOCKS5_PROXY_URL` environment variable (or a default example) and perform the flow:

- Start a local HTTP proxy that bridges to the SOCKS5 upstream.
- Ask FlareSolverr (via that HTTP proxy) to fetch the target URL and return cookies + user-agent.
- Create a Cloudscraper session that uses the local HTTP proxy and the returned cookies.


Files of interest
-----------------

- `proxy_bridge.py` - Implements `SocksToHttpProxy`, converts an upstream SOCKS5 proxy into a local HTTP proxy using aiohttp/aiohttp_socks.
- `flaresolverr_client.py` - Small synchronous wrapper around the local `flaresolver` package to request pages and extract cookies/user-agent.
- `scraper_factory.py` - Orchestrates starting the bridge, calling FlareSolverr to get cookies, and building a configured Cloudscraper session.
- `test.py` - Minimal example that ties everything together.

Cookie refresh behavior (how-to)
-------------------------------

The intended flow for automatic cookie refresh is:

1. Cloudscraper makes a request using the bridged HTTP proxy and cookies obtained from FlareSolverr.
2. If the response status is 403 Forbidden (Cloudflare blocking), call FlareSolverr again (via the same HTTP proxy) to re-run the challenge and obtain fresh cookies + user-agent.
3. Replace the Cloudscraper session's cookies and User-Agent header with the fresh ones and retry the original request.

Implementation notes
--------------------

- The repository already contains the main building blocks. `scraper_factory.create_scraper_from_socks5()` yields a `(scraper, bridge)` tuple. Keep the bridge running while you use the scraper.
- The current code expects an authenticated SOCKS5 upstream proxy. It is trivial to adapt the bridge to accept an unauthenticated SOCKS5 URL (remove username/password) or to skip the bridge and use an HTTP proxy directly by setting `scraper.proxies`.
- FlareSolverr is executed via the included `flaresolver` package (`flaresolverr_client.py` calls into `flaresolver/*`). That code runs a local headless browser to solve challenges; it may take a few seconds per solve.

Security and privacy
--------------------

- The example contains an inline default proxy string for convenience. Do not commit real credentials into public repositories. Prefer using environment variables or GitHub Secrets.
- FlareSolverr will run headless browser processes locally; be mindful of CPU and memory usage.

Next steps (for the repository maintainer)
----------------------------------------

- Add optional support for using an HTTP upstream proxy (with/without auth) directly, bypassing the bridge.
