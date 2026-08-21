from django import forms

from sales.models import ProductCost
from workspaces.models import Shop

from .models import Feedback


class EtsyCSVImportForm(forms.Form):
    shop = forms.ModelChoiceField(queryset=Shop.objects.none(), label="Shop")
    csv_file = forms.FileField(
        label="Etsy Orders CSV",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

    def __init__(self, *args, shops=(), selected_shop=None, **kwargs):
        super().__init__(*args, **kwargs)
        shop_ids = [shop.id for shop in shops]
        self.fields["shop"].queryset = Shop.objects.filter(id__in=shop_ids).order_by("name")
        self.fields["shop"].initial = selected_shop

    def clean_csv_file(self):
        uploaded_file = self.cleaned_data["csv_file"]
        if not uploaded_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("Choose a CSV file.")
        return uploaded_file


class ProductCostForm(forms.ModelForm):
    class Meta:
        model = ProductCost
        fields = ("sku", "title", "materials", "packaging", "labor", "overhead")
        widgets = {
            field: forms.NumberInput(attrs={"step": "0.01", "min": "0"})
            for field in ("materials", "packaging", "labor", "overhead")
        }


class ShopForm(forms.ModelForm):
    class Meta:
        model = Shop
        fields = ("name", "currency")
        widgets = {"currency": forms.TextInput(attrs={"maxlength": "3"})}

    def clean_currency(self):
        return self.cleaned_data["currency"].strip().upper()


class FeedbackForm(forms.ModelForm):
    rating = forms.TypedChoiceField(
        label="How useful is FeeLoom? (1 = worst, 5 = best)",
        coerce=int,
        choices=(
            (1, "1 - Worst"),
            (2, "2"),
            (3, "3 - Neutral"),
            (4, "4"),
            (5, "5 - Best"),
        ),
    )

    class Meta:
        model = Feedback
        fields = ("category", "rating", "message", "page_path")
        labels = {"message": "Tell us what happened"}
        widgets = {
            "message": forms.Textarea(
                attrs={
                    "rows": 6,
                    "placeholder": "What did you expect, and what happened instead?",
                }
            ),
            "page_path": forms.HiddenInput(),
        }

    def clean_page_path(self):
        page_path = self.cleaned_data.get("page_path", "").strip()
        return page_path if page_path.startswith("/") and not page_path.startswith("//") else ""


class DeleteWorkspaceForm(forms.Form):
    workspace_name = forms.CharField(label="Workspace name", max_length=160)
    password = forms.CharField(label="Your password", widget=forms.PasswordInput)

    def __init__(self, *args, user, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.workspace = workspace

    def clean_workspace_name(self):
        workspace_name = self.cleaned_data["workspace_name"].strip()
        if workspace_name != self.workspace.name:
            raise forms.ValidationError("Enter the workspace name exactly as shown.")
        return workspace_name

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Your password is incorrect.")
        return password
