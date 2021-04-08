# Generated by Django 2.2 on 2021-03-28 17:44

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('django_socio_grpc', '0020_grcpmicroservices_error'),
    ]

    operations = [
        migrations.CreateModel(
            name='grpcLogging',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', models.DateTimeField(blank=True)),
                ('modified', models.DateTimeField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('is_delete', models.BooleanField(default=False)),
                ('method', models.IntegerField(choices=[(1, 'List'), (2, 'Create'), (3, 'Retrieve'), (4, 'Update'), (5, 'Destroy')], default=0)),
                ('query', models.TextField(blank=True, default='', null=True)),
                ('elapse', models.FloatField(default=0.0, verbose_name='Elapse Time (sec)')),
                ('database', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='django_socio_grpc.grcpDataBases', verbose_name='Database Microservice')),
                ('service', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, to='django_socio_grpc.grcpMicroServices', verbose_name='Socotec Microservice')),
            ],
            options={
                'verbose_name': 'GRPC LOGGING HANDLER',
                'verbose_name_plural': 'GRPC LOGGING HANDLER',
            },
        ),
    ]
