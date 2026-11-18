"""card-agent: โปรแกรมเล็กบนเครื่องผู้ใช้ เปิด WebSocket ที่ 127.0.0.1 ให้หน้าเว็บ CCID4 ขอข้อมูลบัตรประชาชน (B9).

    python -m card_agent --origin https://ccid4.local

Security: listens on 127.0.0.1 only · accepts only the CCID4 origins given with --origin · reads the card only when
the page asks · keeps nothing on disk · logs no personal data.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging

from . import thai_id

VERSION = "1.0"
log = logging.getLogger("card-agent")


class ReaderError(Exception):
    pass


class PcscReader:
    """Real card reader through PC/SC (pyscard)."""

    def name(self) -> str | None:
        try:
            from smartcard.System import readers
        except ImportError as e:
            raise ReaderError("ยังไม่ได้ติดตั้ง pyscard (pip install pyscard)") from e
        found = readers()
        return str(found[0]) if found else None

    def read(self) -> tuple[dict, bytes]:
        from smartcard.Exceptions import CardConnectionException, NoCardException
        from smartcard.System import readers

        found = readers()
        if not found:
            raise ReaderError("ไม่พบเครื่องอ่านบัตร เสียบเครื่องอ่านแล้วลองใหม่")
        conn = found[0].createConnection()
        try:
            conn.connect()
        except (NoCardException, CardConnectionException) as e:
            raise ReaderError("ยังไม่ได้เสียบบัตร หรือเสียบกลับด้าน") from e
        atr = conn.getATR()

        def send(cmd: list[int]) -> bytes:
            data, sw1, sw2 = conn.transmit(cmd)
            if sw1 == 0x61:
                data, sw1, sw2 = conn.transmit(thai_id.get_response(atr, cmd[-1]))
            if sw1 != 0x90:
                raise ReaderError(f"บัตรตอบกลับผิดพลาด ({sw1:02X}{sw2:02X}) — บัตรนี้อาจไม่ใช่บัตรประชาชน")
            return bytes(data)

        try:
            send(thai_id.SELECT_APPLET)
            raw = {name: thai_id.text(send(thai_id.read_command(name))) for name in thai_id.FIELDS}
            photo = b"".join(send(cmd) for cmd in thai_id.photo_commands()).rstrip(b"\x00")
        finally:
            conn.disconnect()
        return raw, photo


async def handle(ws, reader, allowed: set[str]):
    origin = (ws.request.headers.get("Origin") if hasattr(ws, "request") else ws.request_headers.get("Origin")) or ""
    if origin not in allowed:
        log.warning("refused a page from an origin that is not CCID4")
        await ws.close(code=4003, reason="origin not allowed")
        return
    async for message in ws:
        try:
            req = json.loads(message)
        except ValueError:
            continue
        if req.get("type") == "status":
            try:
                name = await asyncio.to_thread(reader.name)
                await ws.send(json.dumps({"type": "status", "reader": name, "version": VERSION}))
            except ReaderError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}))
        elif req.get("type") == "read":
            try:
                raw, photo = await asyncio.to_thread(reader.read)
                person = thai_id.to_person(raw)
                pic = f"data:image/jpeg;base64,{base64.b64encode(photo).decode()}" if photo.startswith(b"\xff\xd8") else ""
                await ws.send(json.dumps({"type": "card", "data": person, "photo": pic}, ensure_ascii=False))
                log.info("card read for the page (no data logged)")
            except ReaderError as e:
                await ws.send(json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False))


async def serve(host: str, port: int, allowed: set[str], reader=None):
    import websockets

    reader = reader or PcscReader()
    async with websockets.serve(lambda ws, *a: handle(ws, reader, allowed), host, port, max_size=2**20):
        log.info("card-agent %s listening on ws://%s:%s for %s", VERSION, host, port, ", ".join(sorted(allowed)))
        await asyncio.Future()


def main(argv=None):
    p = argparse.ArgumentParser(description="CCID4 card-agent: อ่านบัตรประชาชนให้หน้าเว็บ CCID4 บนเครื่องนี้")
    p.add_argument("--origin", action="append", help="ที่อยู่ระบบ CCID4 ที่อนุญาต (ใส่ได้หลายครั้ง) เช่น https://ccid4.local")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    allowed = set(args.origin or ["https://ccid4.local", "http://localhost:8000", "http://127.0.0.1:8000"])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    asyncio.run(serve("127.0.0.1", args.port, allowed))


if __name__ == "__main__":
    main()
