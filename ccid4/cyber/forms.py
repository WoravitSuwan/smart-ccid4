from django import forms

from .models import BankAccount, CryptoWallet, OnlineAccount

BANKS = ["กสิกรไทย", "กรุงเทพ", "กรุงไทย", "ไทยพาณิชย์", "กรุงศรีอยุธยา", "ทหารไทยธนชาต (ttb)", "ออมสิน", "ธ.ก.ส.", "อาคารสงเคราะห์",
         "ยูโอบี", "ซีไอเอ็มบี ไทย", "เกียรตินาคินภัทร", "แลนด์ แอนด์ เฮ้าส์", "ทิสโก้", "อิสลามแห่งประเทศไทย", "ไอซีบีซี (ไทย)"]  # fmt: skip
DT = forms.DateTimeInput(attrs={"type": "datetime-local", "class": "input"}, format="%Y-%m-%dT%H:%M")


def styled(form):
    for f in form.fields.values():
        css = "input" if not isinstance(f.widget, forms.CheckboxInput) else ""
        f.widget.attrs.setdefault("class", css)
    return form


class TransferForm(forms.Form):
    occurred_at = forms.DateTimeField(
        label="วันเวลา", required=False, widget=DT, input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
    )
    amount = forms.DecimalField(label="จำนวนเงิน (บาท)", min_value=0.01, max_digits=14, decimal_places=2,
                                widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))  # fmt: skip
    from_no = forms.CharField(label="จากบัญชี", required=False, max_length=40, help_text="ว่าง = ผู้เสียหายโอนจากบัญชีที่ยังไม่ระบุ")
    from_bank = forms.CharField(
        label="ธนาคารต้นทาง", required=False, max_length=120, widget=forms.TextInput(attrs={"list": "bank-list"})
    )
    from_name = forms.CharField(label="ชื่อบัญชีต้นทาง", required=False, max_length=200)
    from_victim = forms.BooleanField(label="บัญชีต้นทางเป็นของผู้เสียหาย", required=False, initial=True)
    to_no = forms.CharField(label="ไปบัญชี (ปลายทาง)", max_length=40)
    to_bank = forms.CharField(
        label="ธนาคารปลายทาง", required=False, max_length=120, widget=forms.TextInput(attrs={"list": "bank-list"})
    )
    to_name = forms.CharField(label="ชื่อบัญชีปลายทาง", required=False, max_length=200)
    channel = forms.CharField(
        label="ช่องทาง", required=False, max_length=60, widget=forms.TextInput(attrs={"list": "channel-list"})
    )
    reference = forms.CharField(label="เลขอ้างอิง", required=False, max_length=80)
    evidence = forms.FileField(label="หลักฐาน (สลิป/statement)", required=False)
    slip_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        styled(self)


class AccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ["bank", "account_name", "status", "is_victim", "party", "owner_info", "note"]
        widgets = {"owner_info": forms.Textarea(attrs={"rows": 2}), "bank": forms.TextInput(attrs={"list": "bank-list"})}

    def __init__(self, *a, case=None, **kw):
        super().__init__(*a, **kw)
        self.fields["party"].queryset = case.parties.select_related("person") if case else self.fields["party"].queryset.none()
        self.fields["party"].label_from_instance = lambda cp: f"{cp.get_role_display()} {cp.person.full_name}"
        styled(self)


class OnlineForm(forms.ModelForm):
    class Meta:
        model = OnlineAccount
        fields = ["kind", "value", "display_name", "party", "note"]

    def __init__(self, *a, case=None, **kw):
        super().__init__(*a, **kw)
        self.fields["party"].queryset = case.parties.select_related("person") if case else self.fields["party"].queryset.none()
        self.fields["party"].label_from_instance = lambda cp: f"{cp.get_role_display()} {cp.person.full_name}"
        styled(self)


class WalletForm(forms.ModelForm):
    class Meta:
        model = CryptoWallet
        fields = ["network", "address", "label", "party", "note"]

    def __init__(self, *a, case=None, **kw):
        super().__init__(*a, **kw)
        self.fields["party"].queryset = case.parties.select_related("person") if case else self.fields["party"].queryset.none()
        self.fields["party"].label_from_instance = lambda cp: f"{cp.get_role_display()} {cp.person.full_name}"
        styled(self)


class TxForm(forms.Form):
    network = forms.ChoiceField(label="เครือข่าย", choices=CryptoWallet.Network.choices)
    tx_hash = forms.CharField(label="Tx hash", max_length=100)
    from_address = forms.CharField(label="จากที่อยู่", required=False, max_length=120)
    to_address = forms.CharField(label="ไปที่อยู่", required=False, max_length=120)
    amount = forms.CharField(label="จำนวน", max_length=60, widget=forms.TextInput(attrs={"inputmode": "decimal"}))
    asset = forms.CharField(label="สินทรัพย์", max_length=20, initial="USDT")
    occurred_at = forms.DateTimeField(label="วันเวลา", required=False, widget=DT, input_formats=["%Y-%m-%dT%H:%M"])

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        styled(self)
