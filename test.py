import asyncio
import os
from scraper_factory import create_scraper_from_socks5


SOCKS5_PROXY_URL = os.environ.get("SOCKS5_PROXY_URL", "socks5://username:password@1.2.3.4:1080")


async def main():
	async with create_scraper_from_socks5(SOCKS5_PROXY_URL) as (scraper, bridge):
		print("Bridge HTTP URL:", bridge.http_proxy_url)
		print("Scrape Target:", scraper.flaresolverr_target)
		resp = await asyncio.to_thread(scraper.get, scraper.flaresolverr_target, timeout=30)
		print("Status:", resp.status_code)
		try:
			print("JSON:", resp.json())
		except Exception:
			print("Text length:", len(resp.text or ""))


if __name__ == "__main__":
	asyncio.run(main())
