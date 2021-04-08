# Generated by Django 2.2 on 2021-04-01 21:52

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_socio_grpc', '0033_auto_20210401_2307'),
    ]

    operations = [
        migrations.CreateModel(
            name='GeneralLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(blank=True)),
                ('modified', models.DateTimeField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('is_delete', models.BooleanField(default=False)),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('level', models.CharField(max_length=10)),
                ('message', models.TextField()),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='SpecialLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(blank=True)),
                ('modified', models.DateTimeField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('is_delete', models.BooleanField(default=False)),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('level', models.CharField(max_length=10)),
                ('message', models.TextField()),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
