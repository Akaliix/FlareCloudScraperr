import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Tuple

import cloudscraper

from flaresolverr_client import FlareSolverrClient
from proxy_bridge import SocksToHttpProxy


TARGET_URL = "https://ticketingweb.passo.com.tr/api/passoweb/getcur"


@asynccontextmanager
async def create_scraper_from_socks5(
    socks5_proxy_url: str,
    target_url: str = TARGET_URL,
) -> AsyncIterator[Tuple[cloudscraper.CloudScraper, SocksToHttpProxy]]:
    """Start a local HTTP proxy that routes to the given SOCKS5 proxy,
    obtain Cloudflare cookies via FlareSolverr (using that HTTP proxy),
    and yield a configured CloudScraper instance plus the running bridge.

    The caller is responsible for keeping the bridge alive for the scraper's lifetime.
    """

    bridge = SocksToHttpProxy(proxy_url=socks5_proxy_url)
    await bridge.start()
    print("Started bridge HTTP URL:", bridge.http_proxy_url)
    # Fetch cookies and user-agent via FlareSolverr using the bridge HTTP URL
    def fetch_cookies():
        client = FlareSolverrClient()
        jar, ua = client.get_cookies(proxy_url=bridge.http_proxy_url, target_url=target_url)
        return jar, ua

    jar, ua = await asyncio.to_thread(fetch_cookies)
    print("Obtained cookies and user-agent from FlareSolverr")
    # Create cloudscraper and configure it
    scraper = cloudscraper.create_scraper()
    # apply proxy
    scraper.proxies = {"http": bridge.http_proxy_url, "https": bridge.http_proxy_url}
    scraper.trust_env = False

    # set cookies and user-agent
    scraper.cookies = jar
    if ua:
        scraper.headers.update({"User-Agent": ua})

    # store metadata for possible refresh (used by later modifications)
    scraper.flaresolverr_proxy = socks5_proxy_url
    scraper.flaresolverr_bridge_http = bridge.http_proxy_url
    scraper.flaresolverr_target = target_url
    print("Configured CloudScraper with FlareSolverr cookies and user-agent")
    try:
        yield scraper, bridge
    finally:
        await bridge.close()
