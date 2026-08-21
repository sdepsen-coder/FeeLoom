from django import forms

from sales.models import ProductCost


class EtsyCSVImportForm(forms.Form):
    csv_file = forms.FileField(
        label="Etsy Orders CSV",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

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
