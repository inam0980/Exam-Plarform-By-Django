from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Examapp', '0005_alter_question_question_type_codingtestcase'),
    ]

    operations = [
        migrations.AddField(
            model_name='userrole',
            name='course',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='userrole',
            name='section',
            field=models.CharField(blank=True, default='', max_length=50),
        ),
    ]
