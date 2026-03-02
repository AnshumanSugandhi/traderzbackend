import csv
import os
from django.core.management.base import BaseCommand
from api.models import DMSLocation, BusinessCategory

class Command(BaseCommand):
    help = 'Loads data from dms_master.csv and Category.csv'

    def handle(self, *args, **kwargs):
        # This points to D:\CODE\TRADERZPLANET\UrlValidator\backend\
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        
        # Use the new, simple file names!
        dms_path = os.path.join(base_dir, 'dms_master.csv')
        cat_path = os.path.join(base_dir, 'category.csv')

        # 1. Load Location Data
        self.stdout.write("Loading DMS Locations...")
        if os.path.exists(dms_path):
            with open(dms_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                locations = []
                for row in reader:
                    locations.append(DMSLocation(
                        s_no=int(row['S.No.']),
                        pin_code=row['Pin Code'],
                        city_name=row['City Name'],
                        state=row['State'],
                        country=row['Country'],
                        is_active=(row['Is Active'].strip().lower() == 'true')
                    ))
                # Bulk create is much faster than saving one by one
                DMSLocation.objects.bulk_create(locations, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS("Successfully loaded locations!"))
        else:
            self.stdout.write(self.style.ERROR(f"Could not find {dms_path}"))

        # 2. Load Category Data
        self.stdout.write("Loading Categories...")
        if os.path.exists(cat_path):
            with open(cat_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                categories = []
                for row in reader:
                    # Collect all synonym columns that have data
                    synonyms = [row.get(f'Synonyms {i}', '') for i in range(1, 15) if row.get(f'Synonyms {i}')]
                    
                    categories.append(BusinessCategory(
                        category_on_dms=row.get('Category on DMS', ''),
                        sub_category_on_dms=row.get('Sub Category  on DMS', ''),
                        small_category_on_dms=row.get('Small Category On DMS', ''),
                        synonyms=",".join(synonyms).lower()
                    ))
                BusinessCategory.objects.bulk_create(categories, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS("Successfully loaded categories!"))