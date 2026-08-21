from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from datetime import timedelta
from django.utils import timezone

from .models import BetaInvite


class SignupForm(UserCreationForm):
    invite_code = forms.CharField(max_length=20, label="Beta invite code")
    email = forms.EmailField()
    workspace_name = forms.CharField(max_length=160, label="Business name")
    shop_name = forms.CharField(max_length=160, label="Etsy shop name")
    accept_terms = forms.BooleanField(
        label="I agree to the Terms and acknowledge the Privacy Policy."
    )

    class Meta:
        model = User
        fields = ("invite_code", "username", "email", "workspace_name", "shop_name", "accept_terms")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_invite_code(self):
        code = self.cleaned_data["invite_code"].strip().upper()
        invite = BetaInvite.objects.filter(code__iexact=code).first()
        if not invite or not invite.is_available:
            raise forms.ValidationError("This invite code is invalid, expired, or fully used.")
        return code


class ResendVerificationForm(forms.Form):
    email = forms.EmailField(label="Account email")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class BetaInviteForm(forms.ModelForm):
    valid_for_days = forms.TypedChoiceField(
        label="Valid for",
        coerce=int,
        choices=((7, "7 days"), (14, "14 days"), (30, "30 days")),
        initial=14,
    )

    class Meta:
        model = BetaInvite
        fields = ("label", "max_uses")
        labels = {"label": "Note", "max_uses": "Number of uses"}
        widgets = {"max_uses": forms.NumberInput(attrs={"min": 1, "max": 10})}

    def clean_max_uses(self):
        max_uses = self.cleaned_data["max_uses"]
        if max_uses > 10:
            raise forms.ValidationError("An invite can be used at most 10 times.")
        return max_uses

    def save_for(self, *, workspace, user):
        invite = self.save(commit=False)
        invite.workspace = workspace
        invite.created_by = user
        invite.expires_at = timezone.now() + timedelta(days=self.cleaned_data["valid_for_days"])
        invite.save()
        return invite
