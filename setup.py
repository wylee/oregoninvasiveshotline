from setuptools import setup, find_packages


VERSION = '1.11.0.dev0'


setup(
    name='psu.oit.arc.oregoninvasiveshotline',
    version=VERSION,
    description='Oregon Invasives Hotline',
    author='PSU - OIT - ARC',
    author_email='consultants@pdx.edu',
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'django>=1.8.18,<1.9',
        'django-arcutils>=2.16.0',
        'django-bootstrap-form>=3.2.1',
        'django-cloak',
        'django-haystack>=2.4.1,<2.5',
        'django-local-settings>=1.0b6',
        'django-perms>=2.0.0',
        'django-pgcli',
        'djangorestframework>=3.6.2',
        # 'django-storages>=1.5.2',
        'elasticsearch>=1.9.0,<2.0.0',
        'Markdown>=2.6.8',
        'Pillow>=4.1.0',
        'psycopg2>=2.7.1',
        'pytz>=2017.2',
        'psu.oit.arc.tasks',
    ],
    extras_require={
        'dev': [
            'bpython',
            'coverage',
            'flake8',
            'model_mommy',
        ]
    },
)
