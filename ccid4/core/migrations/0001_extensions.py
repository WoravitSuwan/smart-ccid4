from django.contrib.postgres.operations import CreateExtension, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True
    operations = [TrigramExtension(), CreateExtension("vector")]
