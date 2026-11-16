from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(label="ชื่อผู้ใช้", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True, "class": "input"}))
    password = forms.CharField(label="รหัสผ่าน", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "class": "input"}))
    error_messages = {
        "invalid_login": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง",
        "inactive": "บัญชีนี้ถูกปิดใช้งาน",
    }

    def clean(self):
        username = self.cleaned_data.get("username")
        if username:
            u = User.objects.filter(username=username).only("account_expires_at").first()
            if u and u.is_expired:
                raise forms.ValidationError("บัญชีหมดอายุ ติดต่อผู้ดูแลระบบเพื่อต่ออายุ", code="expired")
        return super().clean()


class TokenForm(forms.Form):
    token = forms.CharField(label="รหัส 6 หลักจากแอปยืนยันตัวตน", min_length=6, max_length=6,
                            widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "autofocus": True, "class": "input"}))
