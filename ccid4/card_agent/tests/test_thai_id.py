"""card-agent: Thai ID parsing and the WebSocket protocol, with a fake reader (no hardware, no network)."""

import asyncio
import json

import pytest

from card_agent import thai_id

SAMPLE = {  # fictional card values in the card's own format (TIS-620 decoded)
    "cid": "1101700203450",
    "name_th": "นางสาว#ทดลอง##ใจดี",
    "name_en": "Miss#Thodlong##Jaidee",
    "birth": "25350214",
    "gender": "2",
    "issuer": "สำนักงานเขตสมมติ",
    "issued": "25660101",
    "expires": "25740213",
    "address": "99/1#หมู่ที่ 4##ซอยสุขใจ#ถนนสมมติ#ตำบลบางรัก#อำเภอเมืองสมมติ#จังหวัดสมมติ",
}


def test_commands_follow_the_card_layout():
    assert thai_id.read_command("cid") == [0x80, 0xB0, 0x00, 0x04, 0x02, 0x00, 0x0D]
    assert thai_id.read_command("address") == [0x80, 0xB0, 0x15, 0x79, 0x02, 0x00, 0x64]
    photo = thai_id.photo_commands()
    assert len(photo) == 20 and photo[0][2:4] == [0x01, 0x7B] and photo[1][2:4] == [0x02, 0x7A]
    assert thai_id.get_response([0x3B, 0x67, 0x00], 13) == [0x00, 0xC0, 0x00, 0x01, 13]
    assert thai_id.get_response([0x3B, 0x79, 0x00], 13) == [0x00, 0xC0, 0x00, 0x00, 13]


def test_text_dates_names_and_address():
    assert thai_id.text("สมมติ".encode("tis_620") + b"\x00\x00  ") == "สมมติ"
    assert thai_id.be_date("25350214") == "1992-02-14" and thai_id.be_date("25350000") == ""
    assert thai_id.split_name("นาย#สมชาย#กลาง#ใจดี") == {
        "title": "นาย",
        "first_name": "สมชาย",
        "middle_name": "กลาง",
        "last_name": "ใจดี",
    }
    a = thai_id.split_address(SAMPLE["address"])
    assert a == {"house_no": "99/1", "moo": "4", "building": "", "soi": "สุขใจ", "road": "สมมติ", "tambon": "บางรัก",
                 "amphoe": "เมืองสมมติ", "province": "สมมติ"}  # fmt: skip
    bkk = thai_id.split_address("12#ถนนสมมติ#แขวงทดลอง#เขตทดสอบ#กรุงเทพมหานคร")
    assert bkk["tambon"] == "ทดลอง" and bkk["amphoe"] == "ทดสอบ" and bkk["province"] == "กรุงเทพมหานคร" and not bkk["building"]


def test_checksum_and_person_fields():
    assert thai_id.valid_cid("1101700203450") and not thai_id.valid_cid("1101700203451")
    p = thai_id.to_person(SAMPLE)
    assert p["title"] == "นางสาว" and p["first_name"] == "ทดลอง" and p["last_name"] == "ใจดี" and p["gender"] == "หญิง"
    assert p["birth_date"] == "1992-02-14" and p["citizen_id"] == "1101700203450" and p["cid_valid"]
    assert p["name_en"] == "Miss Thodlong Jaidee" and p["expires"] == "2031-02-13"


class FakeReader:
    def name(self):
        return "Fake PC/SC reader"

    def read(self):
        return dict(SAMPLE), b"\xff\xd8fictional-jpeg"


def test_websocket_answers_only_the_ccid4_page():
    websockets = pytest.importorskip("websockets")
    from card_agent import agent

    async def scenario():
        server = await websockets.serve(lambda ws, *a: agent.handle(ws, FakeReader(), {"https://ccid4.local"}), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}", origin="https://ccid4.local") as ws:
                await ws.send(json.dumps({"type": "status"}))
                status = json.loads(await ws.recv())
                await ws.send(json.dumps({"type": "read"}))
                card = json.loads(await ws.recv())
            refused = None
            async with websockets.connect(f"ws://127.0.0.1:{port}", origin="https://evil.example") as ws:
                try:
                    await ws.recv()
                except websockets.ConnectionClosed as e:
                    refused = e.rcvd.code if e.rcvd else None
            return status, card, refused
        finally:
            server.close()
            await server.wait_closed()

    status, card, refused = asyncio.run(scenario())
    assert status == {"type": "status", "reader": "Fake PC/SC reader", "version": agent.VERSION}
    assert (
        card["type"] == "card"
        and card["data"]["citizen_id"] == "1101700203450"
        and card["photo"].startswith("data:image/jpeg;base64,")
    )
    assert refused == 4003
