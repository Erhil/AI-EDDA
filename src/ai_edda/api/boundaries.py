import ipaddress
import re
from urllib.parse import urlsplit
from uuid import UUID


def _authority(value):
    if not value or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError
    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)\](?::([0-9]+))?", value)
        if not match:
            raise ValueError
        host = str(ipaddress.IPv6Address(match.group(1)))
        port = match.group(2)
    else:
        match = re.fullmatch(r"([A-Za-z0-9.-]+)(?::([0-9]+))?", value)
        if not match:
            raise ValueError
        host, port = match.groups()
        host = host.lower()
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError
    if port is not None and not 0 < int(port) <= 65535:
        raise ValueError
    return host, int(port) if port is not None else None


def _raw_upload(scope, headers):
    if scope.get("method") == "POST" and scope.get("path") == "/api/projects/imports":
        return True
    if (
        headers.get(b"content-type", [b""])[0].split(b";", 1)[0].strip().lower()
        != b"application/octet-stream"
    ):
        return False
    parts = scope.get("path", "").split("/")
    if (
        len(parts) != 5
        or parts[1:3] != ["api", "projects"]
        or parts[4] not in {"audio-imports", "cover-imports", "native-imports", "game-imports"}
    ):
        return False
    try:
        UUID(parts[3])
        return True
    except ValueError:
        return False


class BoundaryMiddleware:
    def __init__(self, app, allowed_hosts, max_body_bytes=8 * 1024 * 1024):
        self.app = app
        self.allowed_hosts = {_authority(host)[0] for host in allowed_hosts}
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value)

        async def reject(status):
            body = b"Bad Request" if status == 400 else b"Payload Too Large"
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        try:
            if len(headers.get(b"host", [])) != 1:
                raise ValueError
            host, port = _authority(headers[b"host"][0].decode("ascii"))
            if host not in self.allowed_hosts:
                raise ValueError
            origins = headers.get(b"origin", [])
            if len(origins) > 1:
                raise ValueError
            if origins:
                origin = origins[0].decode("ascii")
                if any(ord(c) <= 32 or ord(c) >= 127 for c in origin):
                    raise ValueError
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in ("http", "https")
                    or not parsed.netloc
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                    or "@" in parsed.netloc
                    or "?" in origin
                    or "#" in origin
                ):
                    raise ValueError
                origin_host, origin_port = _authority(parsed.netloc)
                scheme = scope.get("scheme", "http").lower()
                if (
                    parsed.scheme != scheme
                    or origin_host != host
                    or (origin_port or (443 if scheme == "https" else 80))
                    != (port or (443 if scheme == "https" else 80))
                ):
                    raise ValueError
            lengths = headers.get(b"content-length", [])
            if len(lengths) > 1 or (lengths and not re.fullmatch(rb"[0-9]+", lengths[0])):
                raise ValueError
        except (ValueError, UnicodeError):
            return await reject(400)
        if scope.get("method") in ("GET", "HEAD") or _raw_upload(scope, headers):
            return await self.app(scope, receive, send)
        if lengths:
            try:
                declared_length = int(lengths[0])
            except ValueError:
                return await reject(400)
            if declared_length > self.max_body_bytes:
                return await reject(413)
        body_buffer = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":

                async def disconnected():
                    return message

                return await self.app(scope, disconnected, send)
            body = message.get("body", b"")
            if len(body_buffer) + len(body) > self.max_body_bytes:
                return await reject(413)
            body_buffer.extend(body)
            if not message.get("more_body", False):
                break
        replay_pending = True

        async def buffered():
            nonlocal replay_pending
            if replay_pending:
                replay_pending = False
                return {"type": "http.request", "body": bytes(body_buffer), "more_body": False}
            return await receive()

        return await self.app(scope, buffered, send)
