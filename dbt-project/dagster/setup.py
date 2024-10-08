from setuptools import find_packages, setup

setup(
    name="dagster",
    version="0.0.1",
    packages=find_packages(),
    package_data={
        "pipeline": [
            "dbt-project/**/*",
        ],
    },
    install_requires=[
        "dagster",
        "dagster-cloud",
        "dagster-dbt",
        "dbt-redshift<1.9",
    ],
    extras_require={
        "dev": [
            "dagster-webserver",
        ]
    },
)