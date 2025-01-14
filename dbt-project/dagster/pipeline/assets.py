import base64
import datetime
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from io import StringIO

import boto3
import pandas as pd
import psycopg2
import requests
import s3fs
import yaml
from dagster import (
    AssetCheckResult,
    AssetCheckSpec,
    AssetExecutionContext,
    AssetKey,
    AssetMaterialization,
    Definitions,
    Failure,
    MetadataValue,
    Output,
    asset,
    asset_check,
    get_dagster_logger,
    job,
    materialize,
    multi_asset_check,
)
from dagster_aws.s3 import S3Resource
from dagster_dbt import (
    DbtCliResource,
    dbt_assets,
    get_asset_key_for_source,
    get_asset_keys_by_output_name_for_source,
)
from dotenv import load_dotenv
from pipeline.utils import checks_api, get_ids, read_s3
from psycopg2 import sql
from soda.sampler.sample_context import SampleContext
from soda.sampler.sample_ref import SampleRef
from soda.sampler.sampler import Sampler
from soda.scan import Scan
from sqlalchemy import create_engine

from .export_cloudurl import get_urls
from .project import dagsteretl_project

load_dotenv()
connection_string = "postgresql+psycopg2://username:password@localhost/mydatabase"
dataset_names = ["order_items", "orders", "products"]
load_from_s3_ids = [
    "112e62b7-6571-403c-89c8-69b4ffa7e83e",
    "0cdc7648-3e1e-40cf-9f6a-3db06add0027",
    "fcafc3cf-c206-4496-98b1-c7c21ff23611",
]

# S3 config
BUCKET_NAME = "soda-dagster"
FILE_KEYS = [
    "bikes/order_items.csv",
    "bikes/orders.csv",
    "bikes/products.csv",
]
# FILE_KEYS = ['retail_products.csv']

NAMES = [
    "brands.csv",
    "categories.csv",
    "customers.csv",
    "order_items.csv",
    "orders.csv",
    "products.csv",
    "staffs.csv",
    "stocks.csv",
    "stores.csv",
]

# AWS and Redshift credentials
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
REDSHIFT_USER = os.getenv("redshift_user")
REDSHIFT_PASSWORD = os.getenv("redshift_password")
REDSHIFT_HOST = os.getenv("redshift_host")
REDSHIFT_PORT = os.getenv("redshift_port")
REDSHIFT_DB = os.getenv("redshift_db")
IAM_ROLE = os.getenv("redshift_iam")
REGION = os.getenv("region")
SCHEMA = os.getenv("schema")
DEFAULT_DELIMITER = ","  # Default delimiter is comma

connection_string = f"postgresql+psycopg2://{REDSHIFT_USER}:{REDSHIFT_PASSWORD}@{REDSHIFT_HOST}/{REDSHIFT_DB}"
engine = create_engine(connection_string)

# S3 bucket and file details
S3_BUCKET = "soda-dagster"
FILE_PATHS = {
    "brands": "bikes/brands.csv",
    "categories": "bikes/categories.csv",
    "customers": "bikes/customers.csv",
    "order_items": "bikes/order_items.csv",
    "orders": "bikes/orders.csv",
    "products": "bikes/products.csv",
    "staffs": "bikes/staffs.csv",
    "stocks": "bikes/stocks.csv",
    "stores": "bikes/stores.csv",
}


# Table creation queries
TABLE_QUERIES = {
    "brands": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.brands (
        brand_id INT PRIMARY KEY,
        brand_name TEXT
    );
    """,
    "categories": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.categories (
        category_id INT PRIMARY KEY,
        category_name TEXT
    );
    """,
    "customers": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.customers (
        customer_id INT PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        phone TEXT,
        email TEXT,
        street TEXT,
        city TEXT,
        state TEXT,
        zip_code VARCHAR
    );
    """,
    "order_items": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.order_items (
        order_item_id INT PRIMARY KEY,
        order_id INT,
        product_id INT,
        quantity INT,
        list_price DECIMAL,
        discount DECIMAL
    );
    """,
    "orders": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.orders (
        order_id INT PRIMARY KEY,
        customer_id INT,
        order_status TEXT,
        order_date DATE,
        required_date DATE,
        shipped_date DATE,
        store_id INT,
        staff_id INT
    );
    """,
    "products": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.products (
        product_id INT PRIMARY KEY,
        product_name TEXT,
        brand_id INT,
        category_id INT,
        model_year INT,
        list_price DECIMAL
    );
    """,
    "staffs": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.staffs (
        staff_id INT PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        email TEXT,
        phone TEXT,
        active BOOLEAN,
        store_id INT,
        manager_id INT
    );
    """,
    "stocks": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.stocks (
        store_id INT,
        product_id INT,
        quantity INT,
        PRIMARY KEY (store_id, product_id)
    );
    """,
    "stores": f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.stores (
        store_id INT PRIMARY KEY,
        store_name TEXT,
        phone TEXT,
        email TEXT,
        street TEXT,
        city TEXT,
        state TEXT,
        zip_code VARCHAR
    );
    """,
}
checks = """

checks for brands:
  - row_count > 0:
      name: Invalid row count
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 3
  - missing_count(brand_id) = 0:
      name: Brand must have ID
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 1
  - duplicate_count(brand_name) = 0:
      name: Unique brand 
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 1

checks for stores:
  - row_count > 0:
      name: Invalid row count for stores
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Location
        weight: 3
  - missing_count(store_id) = 0:
      name: Store must have ID
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Location
        weight: 2
  - invalid_count(email) = 0:
      name: Email validity

      valid format: email
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Validity
        data_domain: Location
        weight: 1
  - invalid_count(phone) = 0:
      name: Phone number validity

      valid format: phone number
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Validity
        data_domain: Location
        weight: 1

checks for stocks:
  - row_count > 0:
      name: Row count for stocks
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 3
  - values in (store_id) must exist in stores (store_id):
      name: Cross check Store ID
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Consistency
        data_domain: Product
        weight: 2
  - values in (product_id) must exist in products (product_id):
      name: Cross check Product ID

      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Consistency
        data_domain: Product
        weight: 2
  - min(quantity) >= 0:
      name: No negative quantities
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Validity
        data_domain: Product
        weight: 2

checks for products:
  - row_count > 0:
      name: Row count for products
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 3

checks for customers:
  - missing_count(phone) = 0:
      name: Missing phone number
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 1
#   - failed rows:
#       name: Failed rows
#       fail condition: missing_count(phone) > 0
#       attributes:
#         pipeline_stage: Pre-ingestion
#         data_quality_dimension:
#           - Completeness
#         data_domain: Product
#         weight: 1


checks for orders:
    # - freshness(order_date) < 1d:
    #     name: Freshness check
    #     attributes:
    #         pipeline_stage: Pre-ingestion
    #         data_quality_dimension:
    #         - Timeliness
    #         data_domain: Transaction
    #         weight: 1
    - failed rows:
        name: Shipment Late
        fail query: |
            select order_id as failed_orders
            from orders
            where shipped_date < required_date;
        attributes:
            pipeline_stage: Pre-ingestion
            data_quality_dimension:
            - Timeliness
            data_domain: Transaction
            weight: 3
        """
dataset_names = ["customers", "order_items", "orders", "products"]


ingestion_ids = [
    "48724ca5-26ff-49c4-9881-ee57c153cb48",
    "ef1723e2-283c-41dc-bfec-04f35ba275ff",
    "2cd0f7e1-7de2-43f0-b159-c1f0ed51fc8f",
    "2360a71f-0670-40ba-bf7f-68d4d7be270f",
]
specs, result = checks_api(ingestion_ids, ingestion=True)


# Function to find cloud URLs for the parsed table names in the provided JSON data
def find_cloud_url(table_names, json_data):
    table_cloud_urls = {}
    for table in table_names:
        for item in json_data["content"]:
            if item["name"] == table:
                table_cloud_urls[table] = item["cloudUrl"]
                break
    return table_cloud_urls


def create_redshift_connection():
    conn = psycopg2.connect(
        dbname=REDSHIFT_DB,
        user=REDSHIFT_USER,
        password=REDSHIFT_PASSWORD,
        host=REDSHIFT_HOST,
        port=REDSHIFT_PORT,
    )
    return conn


def create_tables(conn):
    with conn.cursor() as cur:
        for table, query in TABLE_QUERIES.items():
            cur.execute(query)
    conn.commit()


def copy_data_from_s3(conn):
    with conn.cursor() as cur:
        for table, file_path in FILE_PATHS.items():
            s3_path = f"s3://{BUCKET_NAME}/{file_path}"
            get_dagster_logger().info(
                f"Copying data from {s3_path} to {SCHEMA}.{table}"
            )
            copy_query = sql.SQL(
                """
                COPY {schema}.{table}
                FROM %s
                IAM_ROLE %s
                FORMAT AS CSV
                IGNOREHEADER 1
                FILLRECORD
                DATEFORMAT 'auto'
                NULL AS 'NULL'
                REGION %s;
            """
            ).format(schema=sql.Identifier(SCHEMA), table=sql.Identifier(table))
            try:
                cur.execute(copy_query, (s3_path, IAM_ROLE, REGION))
                get_dagster_logger().info(
                    f"Data copied successfully to {SCHEMA}.{table}"
                )
            except Exception as e:
                get_dagster_logger().error(
                    f"Error copying data to {SCHEMA}.{table}: {e}"
                )
                conn.rollback()
                raise e
    conn.commit()
    get_dagster_logger().info("All data copied successfully")


def copy_data():
    conn = create_redshift_connection()
    try:
        create_tables(conn)
        copy_data_from_s3(conn)
        get_dagster_logger().info("Data loaded successfully.")
    except Exception as e:
        get_dagster_logger().info(f"Error loading data: {e}")
    finally:
        conn.close()


# URL to make the POST request to
url = "https://demo.soda.io/api/v1/scans"
api_key_id = os.getenv("soda_api_key_id")
api_key_secret = os.getenv("soda_api_key_secret")
credentials = f"{api_key_id}:{api_key_secret}"
encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

# Headers, including the authorization token
headers = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "Authorization": f"Basic {encoded_credentials}",
}

# Data for the POST request
payload = {"scanDefinition": "dagsterredshift_default_scan"}


def trigger_scan():
    response = requests.post(url, headers=headers, data=payload)

    # Check the response status code
    if response.status_code == 201:
        get_dagster_logger().info("Request successful")
        # Print the response content
        scan_id = response.headers.get("X-Soda-Scan-Id")
        if not scan_id:
            get_dagster_logger().info("X-Soda-Scan-Id header not found")
            exit(1)

    else:
        get_dagster_logger().error(
            f"Request failed with status code {response.status_code}"
        )
        print(response.text)
        exit(1)
    # Check the scan status in a loop

    while scan_id:
        get_response = requests.get(f"{url}/{scan_id}", headers=headers)

        if get_response.status_code == 200:
            scan_status = get_response.json()
            state = scan_status.get("state")
            print(f"Scan state: {state}")

            if state in ["queuing", "executing"]:
                # Wait for a few seconds before checking again
                time.sleep(10)
                print(f"Scan state: {state}")
            elif state == "completed":
                print("Scan completed successfully")
                cloud_url = scan_status.get("cloudUrl", "N/A")

                get_dagster_logger().info(f"Scan: {state} successfully")
                return state, cloud_url

            else:
                print(f"Scan failed with state: {state}")
                cloud_url = scan_status.get("cloudUrl", "N/A")

                get_dagster_logger().info(f"Scan failed with status: {state}")
                return state, cloud_url
        else:
            print(f"GET request failed with status code {get_response.status_code}")
            print(get_response.text)
            get_dagster_logger().info(
                f"GET request failed with status code {get_response.status_code}"
            )
            exit(1)


class CustomSampler(Sampler):
    def store_sample(self, sample_context: SampleContext):
        sample_schema = sample_context.sample.get_schema()
        rows = sample_context.sample.get_rows()
        json_data = json.dumps(rows)  # Convert failed rows to JSON
        exceptions_df = pd.read_json(json_data)  # create dataframe with failed rows
        # Define exceptions dataframe
        exceptions_schema = sample_context.sample.get_schema().get_dict()
        exception_df_schema = []
        for n in exceptions_schema:
            exception_df_schema.append(n["name"])
        exceptions_df.columns = exception_df_schema
        check_name = sample_context.check_name
        exceptions_df["failed_check"] = check_name
        exceptions_df["created_at"] = datetime.now()
        exceptions_df.to_csv(
            check_name + ".csv", sep=",", index=False, encoding="utf-8"
        )
        bytestowrite = exceptions_df.to_csv(None).encode()

        fs = s3fs.S3FileSystem(key=AWS_ACCESS_KEY, secret=AWS_SECRET_KEY)
        with fs.open(f"s3://soda-dagster/failed_rows/{check_name}.csv", "wb") as f:
            f.write(bytestowrite)
        get_dagster_logger().info(f"Successfuly sent failed rows to {check_name}.csv ")
        return SampleRef(
            name=sample_context.sample_name,
            schema=sample_schema,
            total_row_count=100,
            stored_row_count=100,
            type=SampleRef.TYPE_PYTHON_CUSTOM_SAMPLER,
            link=os.getenv("s3_bucket"),
            message=f"Access failed row samples for {sample_context.partition.table.table_name} in external file storage.",
            link_text="S3 Bucket",
        )


@asset(check_specs=specs, compute_kind='python')
def s3_files(context):
    failed_rows_cloud = "false"
    # Initialize Soda Scan
    scan = Scan()
    scan.set_scan_definition_name("Soda Dagster Demo")
    scan.set_data_source_name("soda-dagster")

    # Add DataFrames to Soda Scan in a loop
    try:
        for i, dataset_name in enumerate(dataset_names, start=1):
            scan.add_pandas_dataframe(
                dataset_name=dataset_name,
                pandas_df=read_s3(f"bikes/{dataset_name}.csv"),
                data_source_name="soda-dagster",
            )
    except KeyError as e:
        get_dagster_logger().error(
            f"DataFrame missing for index {e}. Check if all files are loaded correctly."
        )

    scan.add_configuration_yaml_file("pipeline/redshift_config.yml")

    scan.add_sodacl_yaml_files("pipeline/checks")
    scan.add_variables({"DATE": "2016-01-03"})

    if failed_rows_cloud == "false":
        scan.sampler = CustomSampler()

    scan.execute()

    api_specs, api_result = checks_api(ingestion_ids, ingestion=True)

    for spec, (name, result, url) in zip(api_specs, api_result):
        passed = True if result == "pass" else False
        yield AssetCheckResult(
            passed=passed,
            metadata={"cloudUrl": MetadataValue.url(url)},
            asset_key=spec.asset_key,
            check_name=spec.name,
        )
    yield Output(None)


recon_specs, api_result = checks_api(load_from_s3_ids)


@asset(deps=[s3_files], compute_kind="python", check_specs=recon_specs)
def load_from_s3(context):
    copy_data()
    scan = Scan()
    scan.set_scan_definition_name("Soda Dagster Demo")
    scan.set_data_source_name("dagsterredshift")
    scan.add_configuration_yaml_file("pipeline/redshift_config.yml")
    for name in dataset_names:
        df = read_s3(f"bikes/{name}.csv")
        scan.add_pandas_dataframe(
            dataset_name=f"{name}", pandas_df=df, data_source_name="soda-dagster"
        )

    scan.add_sodacl_yaml_files("pipeline/recon")

    scan.sampler = CustomSampler()

    scan.execute()

    api_specs, api_result = checks_api(load_from_s3_ids)
    get_dagster_logger().info(recon_specs)

    for spec, (name, result, url) in zip(api_specs, api_result):
        passed = True if result == "pass" else False
        yield AssetCheckResult(
            passed=passed,
            metadata={"cloudUrl": MetadataValue.url(url)},
            asset_key=spec.asset_key,
            check_name=spec.name,
        )
    yield Output(None)


@dbt_assets(select="marts", manifest=dagsteretl_project.manifest_path)
def dbt_staging(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(
        ["build"], context=context, manifest=dagsteretl_project.manifest_path
    ).stream()


@multi_asset_check(
    specs=[
        AssetCheckSpec("soda_UI_check", asset=AssetKey(["staging", "t_sales_summary"])),
        AssetCheckSpec(
            "soda_UI_check", asset=AssetKey(["staging", "t_product_popularity"])
        ),
    ]
)
def soda_UI_check():
    state, cloudurl = trigger_scan()
    all_passed = True
    passed_sales_summary = bool(state == "completed")
    if not passed_sales_summary:
        all_passed = False
    yield AssetCheckResult(
        passed=passed_sales_summary,
        asset_key=AssetKey(["staging", "t_sales_summary"]),
        metadata={"cloudUrl": MetadataValue.url(cloudurl)},
    )
    passed_product_popularity = bool(state == "completed")
    if not passed_product_popularity:
        all_passed = False

    yield AssetCheckResult(
        passed=passed_product_popularity,
        asset_key=AssetKey(["staging", "t_product_popularity"]),
        metadata={"cloudUrl": MetadataValue.url(cloudurl)},
    )
    if all_passed:
        yield AssetMaterialization(asset_key=AssetKey(["soda_UI_check"]))
    else:
        sys.exit(1)
        raise Failure(
            "One or more Soda Cloud checks failed. Please check your Soda Cloud account for more details."
        )


@asset(deps=[dbt_staging])
def process_data():
    sys.exit(1)


@asset(deps=[process_data], io_manager_key=None)
def reporting():
    return


# @dbt_assets(select="prod", manifest=dagsteretl_project.manifest_path)
# def dbt_prod(context: AssetExecutionContext, dbt: DbtCliResource):
#     yield from dbt.cli(
#         ["build"], context=context, manifest=dagsteretl_project.manifest_path
#     ).stream()
