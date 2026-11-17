from django.conf import settings
from django.db import models

from core import thai

from .identifiers import KINDS


class BankAccount(models.Model):
    """บัญชีในเส้นทางเงิน: บัญชีผู้เสียหาย หรือบัญชีปลายทาง (บัญชีม้า) (B3)."""

    class Status(models.TextChoices):
        NOT_REQUESTED = "not_requested", "ยังไม่ขอข้อมูล"
        REQUESTED = "requested", "ขอข้อมูลแล้ว"
        FROZEN = "frozen", "ระงับแล้ว"
        OWNER = "owner_received", "ได้รับข้อมูลเจ้าของ"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="bank_accounts", verbose_name="คดี")
    bank = models.CharField("ธนาคาร", max_length=120, blank=True)
    account_no = models.CharField("เลขบัญชี", max_length=40)
    norm = models.CharField("เลขบัญชี (ตัวเลขล้วน)", max_length=40, db_index=True)
    account_name = models.CharField("ชื่อบัญชี", max_length=200, blank=True)
    is_victim = models.BooleanField("บัญชีของผู้เสียหาย", default=False)
    party = models.ForeignKey("parties.CaseParty", null=True, blank=True, on_delete=models.SET_NULL, related_name="bank_accounts",
                              verbose_name="เจ้าของบัญชีในคดี")  # fmt: skip
    status = models.CharField("สถานะ", max_length=20, choices=Status.choices, default=Status.NOT_REQUESTED)
    owner_info = models.TextField("ข้อมูลเจ้าของบัญชีที่ได้รับจากธนาคาร", blank=True)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "บัญชีธนาคารในเส้นทางเงิน"
        verbose_name_plural = "บัญชีธนาคารในเส้นทางเงิน"
        ordering = ["-is_victim", "bank", "id"]
        constraints = [models.UniqueConstraint(fields=["case", "norm"], name="cyber_account_once_per_case")]

    def __str__(self):
        return f"{self.bank} {self.masked}".strip()

    @property
    def masked(self) -> str:
        return thai.mask_account(self.account_no)


class MoneyTransfer(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "กรอกเอง"
        IMPORT = "import", "นำเข้าไฟล์"
        SLIP = "slip", "อ่านจากสลิป (ตรวจทานแล้ว)"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="transfers", verbose_name="คดี")
    occurred_at = models.DateTimeField("วันเวลา", null=True, blank=True)
    from_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="outgoing", verbose_name="จากบัญชี"
    )
    to_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="incoming", verbose_name="ไปบัญชี")
    amount = models.DecimalField("จำนวนเงิน (บาท)", max_digits=14, decimal_places=2)
    channel = models.CharField("ช่องทาง", max_length=60, blank=True)
    reference = models.CharField("เลขอ้างอิง", max_length=80, blank=True)
    evidence = models.FileField("หลักฐาน (สลิป/statement)", upload_to="cyber/evidence/%Y/%m/", blank=True)
    source = models.CharField("ที่มา", max_length=10, choices=Source.choices, default=Source.MANUAL)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ธุรกรรม"
        verbose_name_plural = "ธุรกรรม"
        ordering = ["occurred_at", "id"]


class OnlineAccount(models.Model):
    """บัญชีโซเชียล/LINE ID/เว็บไซต์/อีเมล ที่เกี่ยวกับคดี (ตัวระบุ B4)."""

    class Kind(models.TextChoices):
        LINE = "line", "LINE ID"
        FACEBOOK = "facebook", "Facebook"
        TIKTOK = "tiktok", "TikTok"
        INSTAGRAM = "instagram", "Instagram"
        TELEGRAM = "telegram", "Telegram"
        X = "x", "X (Twitter)"
        URL = "url", "เว็บไซต์/URL"
        EMAIL = "email", "อีเมล"
        OTHER = "other", "อื่น ๆ"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="online_accounts", verbose_name="คดี")
    party = models.ForeignKey("parties.CaseParty", null=True, blank=True, on_delete=models.SET_NULL, related_name="online_accounts",
                              verbose_name="เป็นของ")  # fmt: skip
    kind = models.CharField("ประเภท", max_length=20, choices=Kind.choices)
    value = models.CharField("ชื่อบัญชี/ID/URL", max_length=300)
    display_name = models.CharField("ชื่อที่แสดง", max_length=200, blank=True)
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "บัญชีออนไลน์/URL"
        verbose_name_plural = "บัญชีออนไลน์/URL"
        ordering = ["kind", "id"]

    @property
    def identifier_kind(self) -> str:
        return {"url": "url", "email": "email"}.get(self.kind, "social")


class CryptoWallet(models.Model):
    class Network(models.TextChoices):
        BTC = "btc", "Bitcoin (BTC)"
        EVM = "evm", "Ethereum/EVM (ETH, BSC, Polygon)"
        TRON = "tron", "TRON (TRX, USDT-TRC20)"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="crypto_wallets", verbose_name="คดี")
    party = models.ForeignKey("parties.CaseParty", null=True, blank=True, on_delete=models.SET_NULL, related_name="crypto_wallets",
                              verbose_name="เป็นของ")  # fmt: skip
    network = models.CharField("เครือข่าย", max_length=10, choices=Network.choices)
    address = models.CharField("ที่อยู่กระเป๋า", max_length=120)
    label = models.CharField("ชื่อเรียก", max_length=120, blank=True, help_text="เช่น กระเป๋าที่ผู้เสียหายโอนไป, กระเป๋าใน Exchange")
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "กระเป๋าคริปโต"
        verbose_name_plural = "กระเป๋าคริปโต"
        ordering = ["network", "id"]
        constraints = [models.UniqueConstraint(fields=["case", "network", "address"], name="cyber_wallet_once_per_case")]


class CryptoTx(models.Model):
    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="crypto_txs", verbose_name="คดี")
    network = models.CharField("เครือข่าย", max_length=10, choices=CryptoWallet.Network.choices)
    tx_hash = models.CharField("Tx hash", max_length=100)
    from_address = models.CharField("จากที่อยู่", max_length=120, blank=True)
    to_address = models.CharField("ไปที่อยู่", max_length=120, blank=True)
    amount = models.DecimalField("จำนวน", max_digits=38, decimal_places=18)
    asset = models.CharField("สินทรัพย์", max_length=20, default="USDT", help_text="เช่น BTC, ETH, USDT")
    occurred_at = models.DateTimeField("วันเวลา", null=True, blank=True)
    source = models.CharField("ที่มา", max_length=10, choices=[("manual", "กรอกเอง"), ("import", "นำเข้า CSV")], default="manual")
    note = models.CharField("หมายเหตุ", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ธุรกรรมคริปโต"
        verbose_name_plural = "ธุรกรรมคริปโต"
        ordering = ["occurred_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["case", "network", "tx_hash", "to_address"], name="cyber_tx_once_per_case")
        ]


class Identifier(models.Model):
    """ดัชนีตัวระบุข้ามคดี (B4): เก็บเฉพาะ HMAC และค่าปิดบัง ไม่เก็บค่าจริง · สร้างใหม่ทุกครั้งที่ข้อมูลคดีเปลี่ยน."""

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="identifiers", verbose_name="คดี")
    party = models.ForeignKey("parties.CaseParty", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField("ประเภท", max_length=20, choices=list(KINDS.items()))
    digest = models.CharField("HMAC", max_length=64)
    masked = models.CharField("แสดงแบบปิดบัง", max_length=120)
    source = models.CharField("ที่มาในคดี", max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ตัวระบุ"
        verbose_name_plural = "ตัวระบุ"
        indexes = [models.Index(fields=["kind", "digest"])]


class MergeProposal(models.Model):
    """ปุ่ม "เสนอรวมสำนวน" (B4): ส่งให้หัวหน้างานสอบสวนพิจารณา."""

    class Status(models.TextChoices):
        PENDING = "pending", "รอพิจารณา"
        APPROVED = "approved", "เห็นชอบให้รวม"
        REJECTED = "rejected", "ไม่รวม"

    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="merge_proposals", verbose_name="คดีที่เสนอ")
    other = models.ForeignKey("cases.Case", on_delete=models.CASCADE, related_name="+", verbose_name="คดีที่จะรวมด้วย")
    reason = models.TextField("เหตุผล")
    status = models.CharField("สถานะ", max_length=10, choices=Status.choices, default=Status.PENDING)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+", verbose_name="ผู้เสนอ")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField("ความเห็นหัวหน้า", max_length=500, blank=True)

    class Meta:
        verbose_name = "คำขอรวมสำนวน"
        verbose_name_plural = "คำขอรวมสำนวน"
        ordering = ["-created_at"]
