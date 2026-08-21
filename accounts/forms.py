from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class SignupForm(UserCreationForm):
    email = forms.EmailField()
    workspace_name = forms.CharField(max_length=160, label="Business name")
    shop_name = forms.CharField(max_length=160, label="Etsy shop name")
    accept_terms = forms.BooleanField(
        label="I agree to the Terms and acknowledge the Privacy Policy."
    )

    class Meta:
        model = User
        fields = ("username", "email", "workspace_name", "shop_name", "accept_terms")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email
