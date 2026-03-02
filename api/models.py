from django.db import models

class DMSLocation(models.Model):
    # We use S.No. from your CSV as the internal ID
    s_no = models.IntegerField(primary_key=True)
    pin_code = models.CharField(max_length=10, db_index=True)
    city_name = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    country = models.CharField(max_length=50, default="India")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.pin_code} - {self.city_name}"

class BusinessCategory(models.Model):
    category_on_dms = models.CharField(max_length=100)
    sub_category_on_dms = models.CharField(max_length=100)
    small_category_on_dms = models.CharField(max_length=100)
    synonyms = models.TextField(help_text="Comma-separated synonyms for matching")

    def __str__(self):
        return self.small_category_on_dms