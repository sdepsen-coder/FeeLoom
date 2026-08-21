from django import forms

from sales.models import ProductCost


class ProductCostForm(forms.ModelForm):
    class Meta:
        model = ProductCost
        fields = ("sku", "title", "materials", "packaging", "labor", "overhead")
        widgets = {
            field: forms.NumberInput(attrs={"step": "0.01", "min": "0"})
            for field in ("materials", "packaging", "labor", "overhead")
        }
