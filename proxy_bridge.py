import asyncio
import logging
from typing import Optional, Tuple
from urllib.parse import urlsplit

import aiohttp
from aiohttp_socks import ProxyConnector, open_connection
from multidict import CIMultiDict


_DISCONNECT_ERRORS = (
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    OSError,
)


class ClientDisconnected(Exception):
    """Raised when the downstream client disconnects while handling a request."""

_LOGGER = logging.getLogger(__name__)


class SocksToHttpProxy:
    """Expose a SOCKS5 proxy as a local HTTP proxy using aiohttp."""

    def __init__(
        self,
        proxy_url: str,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        request_timeout: float = 30.0,
    ) -> None:
        if not proxy_url.startswith("socks5"):
            raise ValueError("Only SOCKS5 proxies are supported")

        self.proxy_url = proxy_url
        self.host = host
        self.port = port
        self._bound_port: Optional[int] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector = ProxyConnector.from_url(proxy_url, limit=32)
        self._request_timeout = aiohttp.ClientTimeout(total=request_timeout)

    @property
    def address(self) -> Tuple[str, int]:
        if self._bound_port is None:
            raise RuntimeError("Proxy bridge is not running")
        return self.host, self._bound_port

    @property
    def http_proxy_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}"

    async def start(self) -> None:
        if self._server is not None:
            return

        loop = asyncio.get_running_loop()
        self._session = aiohttp.ClientSession(
            connector=self._connector,
            timeout=self._request_timeout,
            auto_decompress=False,
            trust_env=False,
        )
        try:
            self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        except Exception:
            await self._session.close()
            self._session = None
            raise

        sockets = self._server.sockets
        if sockets:
            self._bound_port = sockets[0].getsockname()[1]
            _LOGGER.info(
                "SOCKS5 proxy %s exposed as HTTP proxy on %s:%s",
                self.proxy_url,
                self.host,
                self._bound_port,
            )
        else:
            raise RuntimeError("Failed to bind proxy bridge socket")

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._bound_port = None

        if self._session:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> "SocksToHttpProxy":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            try:
                method, raw_target, version = request_line.decode("latin-1").strip().split()
            except ValueError:
                await self._send_error(writer, "HTTP/1.1", 400, "Bad Request")
                return

            headers, headers_lower = await self._read_headers(reader)

            if method.upper() == "CONNECT":
                await self._handle_connect(raw_target, version, reader, writer)
                return

            expect_header = headers_lower.get("expect")
            if expect_header and expect_header.lower() == "100-continue":
                try:
                    writer.write(f"{version} 100 Continue\r\n\r\n".encode("latin-1"))
                    await writer.drain()
                except _DISCONNECT_ERRORS:
                    return

            body = await self._read_body(reader, headers_lower)

            await self._handle_http_request(
                method,
                raw_target,
                version,
                headers,
                headers_lower,
                body,
                writer,
            )
        except asyncio.IncompleteReadError:
            pass
        except ClientDisconnected:
            pass
        except Exception as exc:  # pragma: no cover - defensive logging
            _LOGGER.exception("HTTP proxy error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # pragma: no cover - best effort
                pass

    async def _handle_http_request(
        self,
        method: str,
        raw_target: str,
        version: str,
        headers: Tuple[Tuple[str, str], ...],
        headers_lower: dict,
        body: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        if not self._session:
            raise RuntimeError("Proxy session is not ready")

        target_url = self._build_target_url(raw_target, headers_lower)
        if target_url is None:
            await self._send_error(writer, version, 400, "Invalid target URL")
            return

        filtered_headers = CIMultiDict()
        for key, value in headers:
            kl = key.lower()
            if kl in {"proxy-connection", "proxy-authorization"}:
                continue
            filtered_headers.add(key, value)

        try:
            async with self._session.request(
                method,
                target_url,
                headers=filtered_headers,
                data=body if body else None,
                allow_redirects=False,
            ) as response:
                try:
                    await self._write_response_head(version, response, writer)
                except ClientDisconnected:
                    _LOGGER.debug("Client disconnected before headers could be sent")
                    return

                async for chunk in response.content.iter_chunked(65536):
                    try:
                        writer.write(chunk)
                        await writer.drain()
                    except _DISCONNECT_ERRORS as exc:
                        _LOGGER.debug("Client disconnected while streaming response: %s", exc)
                        return
        except ClientDisconnected:
            _LOGGER.debug("Client disconnected while sending error response")
            return
        except Exception as exc:
            _LOGGER.debug("Upstream request failed: %s", exc)
            try:
                await self._send_error(writer, version, 502, "Bad Gateway")
            except ClientDisconnected:
                _LOGGER.debug("Client disconnected before error response could be sent")

    async def _handle_connect(
        self,
        raw_target: str,
        version: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            host, port_str = raw_target.split(":", 1)
            port = int(port_str)
        except ValueError:
            await self._send_error(client_writer, version, 400, "Invalid CONNECT target")
            return

        try:
            remote_reader, remote_writer = await open_connection(
                proxy_url=self.proxy_url,
                host=host,
                port=port,
            )
        except Exception as exc:
            _LOGGER.debug("Tunnel setup failed: %s", exc)
            await self._send_error(client_writer, version, 502, "Bad Gateway")
            return

        # Notify the client that the tunnel is ready before piping traffic.
        client_writer.write(f"{version} 200 Connection Established\r\n".encode("latin-1"))
        client_writer.write(b"Proxy-Agent: SocksToHttpProxy\r\n\r\n")
        await client_writer.drain()

        async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            except Exception:  # pragma: no cover - shutdown path
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        await asyncio.gather(
            pipe(client_reader, remote_writer),
            pipe(remote_reader, client_writer),
            return_exceptions=True,
        )

    async def _read_headers(
        self, reader: asyncio.StreamReader
    ) -> Tuple[Tuple[Tuple[str, str], ...], dict]:
        headers = []
        while True:
            line = await reader.readline()
            if not line:
                break
            if line in (b"\r\n", b"\n"):
                break
            try:
                key, value = line.decode("latin-1").split(":", 1)
            except ValueError:
                continue
            headers.append((key.strip(), value.strip()))
        headers_lower = {key.lower(): value for key, value in headers}
        return tuple(headers), headers_lower

    async def _read_body(self, reader: asyncio.StreamReader, headers_lower: dict) -> bytes:
        length_header = headers_lower.get("content-length")
        if not length_header:
            return b""
        try:
            length = int(length_header)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return await reader.readexactly(length)

    def _build_target_url(self, raw_target: str, headers_lower: dict) -> Optional[str]:
        if raw_target.startswith("http://") or raw_target.startswith("https://"):
            return raw_target

        host = headers_lower.get("host")
        if not host:
            return None

        path = raw_target if raw_target else "/"
        if not path.startswith("/"):
            path = f"/{path}"
        return f"http://{host}{path}"

    async def _write_response_head(
        self,
        version: str,
        response: aiohttp.ClientResponse,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            status_line = f"{version} {response.status} {response.reason}\r\n"
            writer.write(status_line.encode("latin-1"))
            for header_name, header_value in response.raw_headers:
                writer.write(header_name + b": " + header_value + b"\r\n")
            writer.write(b"\r\n")
            await writer.drain()
        except _DISCONNECT_ERRORS as exc:
            raise ClientDisconnected from exc

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        version: str,
        status: int,
        reason: str,
    ) -> None:
        body = reason.encode("latin-1")
        response = (
            f"{version} {status} {reason}\r\n"
            "Content-Type: text/plain; charset=latin-1\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1") + body
        try:
            writer.write(response)
            await writer.drain()
        except _DISCONNECT_ERRORS as exc:
            raise ClientDisconnected from exc


def parse_proxy_url(proxy_url: str) -> Tuple[str, int, Optional[str], Optional[str]]:
    """Parse SOCKS proxy URL."""
    parsed = urlsplit(proxy_url)
    if not parsed.hostname or not parsed.port:
        raise ValueError("Proxy URL must include host and port")
    return parsed.hostname, parsed.port, parsed.username, parsed.password
