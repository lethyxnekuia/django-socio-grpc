# Generated by Django 2.2 on 2021-03-26 16:09

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('django_socio_grpc', '0009_grcpdatabases_service'),
    ]

    operations = [
        migrations.AlterField(
            model_name='grcpdatabases',
            name='django',
            field=models.CharField(choices=[('django.contrib.admin', 'Administration'), ('django.contrib.auth', 'Authentication and Authorization'), ('django.contrib.contenttypes', 'Content Types'), ('django.contrib.sessions', 'Sessions'), ('django.contrib.messages', 'Messages'), ('django.contrib.staticfiles', 'Static Files'), ('django_socio_grpc', 'django_socio_grpc'), ('carcheck', 'Carcheck'), ('user', 'User')], db_index=True, default='', max_length=40, verbose_name='Django Application'),
        ),
    ]