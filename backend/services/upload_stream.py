"""Reads ONE file out of a ``multipart/form-data`` request while it streams in (fin-02).

FastAPI's ``UploadFile`` parses the whole body before the endpoint runs, so a 2 GB upload
would be accepted to disk before anyone could say "too big". This reader feeds the request
stream to ``python-multipart`` chunk by chunk and answers 413 the moment the file passes
the limit -- the rest of the body is never read.

Two caps apply: the FILE (``max_bytes``) and the whole BODY (``max_bytes`` + ``slack``, which
pays for the multipart envelope: boundaries, headers, a file name). A ``Content-Length`` over
the body cap is refused before a single byte is read. A third bounds the TIME: the whole body
must arrive within ``READ_TIMEOUT_SECONDS`` (408), so a client that dribbles bytes cannot hold a
connection -- and an upload slot -- for ever. A client that hangs up mid-body ends the request
quietly (``ClientGone``, 499): that is not a server error and not worth a Sentry event.

Only the part called ``field`` is kept; other parts are skipped (they still count toward
the body cap and the part limit). Nothing here logs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from python_multipart.multipart import MultipartParser, parse_options_header
from python_multipart.exceptions import MultipartParseError
from starlette.requests import ClientDisconnect, Request

from services.finance import ClientGone, PayloadTooLarge, RequestTimeout, Unprocessable

ENVELOPE_SLACK = 64 * 1024
READ_TIMEOUT_SECONDS = 120
MAX_PARTS = 8
MAX_HEADER_BYTES = 8 * 1024
MAX_BOUNDARY_LENGTH = 200
BYTES_IN_MB = 1024 * 1024

_NEED_MULTIPART = "Ожидается multipart/form-data с полем file"


@dataclass(frozen=True)
class UploadedFile:
    # The collector's own buffer, handed over as it is: a ``bytes()`` copy would double a 10 MB upload.
    data: bytes | bytearray
    filename: str | None


def _too_big(limit: int) -> PayloadTooLarge:
    return PayloadTooLarge(f"Файл больше {limit // BYTES_IN_MB} МБ")


class _Collector:
    """The parser's callbacks: remembers the one wanted part, counts and refuses the rest."""

    def __init__(self, field: str, max_bytes: int) -> None:
        self._field = field.encode()
        self._max_bytes = max_bytes
        self._parts = 0
        self._header_name = bytearray()
        self._header_value = bytearray()
        self._headers: dict[bytes, bytes] = {}
        self._header_size = 0
        self._collecting = False
        self.found = False
        self.closed = False
        self.filename: str | None = None
        self.data = bytearray()

    def callbacks(self) -> dict:
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": self._header_field,
            "on_header_value": self._header_value_chunk,
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_finished,
            "on_part_data": self._part_data,
            "on_part_end": self._part_end,
        }

    def _part_begin(self) -> None:
        self._parts += 1
        if self._parts > MAX_PARTS:
            raise Unprocessable("Слишком много полей в запросе")
        self._headers = {}
        self._header_size = 0
        self._collecting = False

    def _count_header_bytes(self, size: int) -> None:
        self._header_size += size
        if self._header_size > MAX_HEADER_BYTES:
            raise Unprocessable("Слишком длинные заголовки поля")

    def _header_field(self, data: bytes, start: int, end: int) -> None:
        self._count_header_bytes(end - start)
        self._header_name += data[start:end]

    def _header_value_chunk(self, data: bytes, start: int, end: int) -> None:
        self._count_header_bytes(end - start)
        self._header_value += data[start:end]

    def _header_end(self) -> None:
        self._headers[bytes(self._header_name).lower()] = bytes(self._header_value)
        self._header_name = bytearray()
        self._header_value = bytearray()

    def _headers_finished(self) -> None:
        _, options = parse_options_header(self._headers.get(b"content-disposition", b""))
        if options.get(b"name") != self._field:
            return
        if self.found:
            raise Unprocessable("Поле file должно быть одно")
        self.found = True
        self._collecting = True
        raw_name = options.get(b"filename")
        self.filename = raw_name.decode("utf-8", errors="replace") if raw_name is not None else None

    def _part_data(self, data: bytes, start: int, end: int) -> None:
        if not self._collecting:
            return
        if len(self.data) + (end - start) > self._max_bytes:
            raise _too_big(self._max_bytes)
        self.data += memoryview(data)[start:end]  # no temporary slice copy

    def _part_end(self) -> None:
        if self._collecting:
            self.closed = True
            self._collecting = False


def _declared_length(request: Request) -> int | None:
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        return None
    return declared if declared >= 0 else None


async def read_single_file(
    request: Request, *, field: str, max_bytes: int, slack: int = ENVELOPE_SLACK
) -> UploadedFile:
    """The file in the multipart part named ``field``.

    Raises:
        PayloadTooLarge: 413 -- the declared body, the streamed body or the file is over its cap.
        Unprocessable: 422 -- not multipart, no such part, two such parts, or a broken body.
        RequestTimeout: 408 -- the body did not arrive within ``READ_TIMEOUT_SECONDS``.
        ClientGone: 499 -- the client hung up before the body was complete.
    """
    body_cap = max_bytes + slack
    declared = _declared_length(request)
    if declared is not None and declared > body_cap:
        raise _too_big(max_bytes)  # refused before the body is touched

    content_type, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary or len(boundary) > MAX_BOUNDARY_LENGTH:
        raise Unprocessable(_NEED_MULTIPART)

    collector = _Collector(field, max_bytes)
    parser = MultipartParser(boundary, collector.callbacks())
    received = 0
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                received += len(chunk)
                if received > body_cap:
                    raise _too_big(max_bytes)
                parser.write(chunk)
            parser.finalize()
    except ClientDisconnect as exc:
        raise ClientGone("Клиент закрыл соединение до конца загрузки") from exc
    except TimeoutError as exc:
        raise RequestTimeout("Загрузка заняла слишком много времени, попробуйте ещё раз") from exc
    except MultipartParseError as exc:
        raise Unprocessable("Некорректное тело запроса") from exc

    if not collector.found:
        raise Unprocessable(_NEED_MULTIPART)
    if not collector.closed:
        raise Unprocessable("Запрос оборван: файл получен не полностью")
    return UploadedFile(data=collector.data, filename=collector.filename)
