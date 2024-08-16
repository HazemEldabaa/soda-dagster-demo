from dagster import AssetExecutionContext
from dagster_dbt import (
    DbtCliResource,
    dbt_assets,
    get_asset_key_for_source,
    get_asset_keys_by_output_name_for_source,
)

from .project import dagsteretl_project


from io import StringIO
import boto3
import pandas as pd
from dagster import (
    asset,
    materialize,
    Output,
    Definitions,
    get_dagster_logger,
    job,
    MetadataValue,
    Failure,
    AssetExecutionContext,
)
from dagster_aws.s3 import S3Resource
from soda.sampler.sampler import Sampler
from soda.sampler.sample_context import SampleContext
import psycopg2
from psycopg2 import sql

# from .resources import dbt_resource
from soda.scan import Scan
from dotenv import load_dotenv
import base64
import subprocess
import os
import requests
import json
import time
import datetime
import s3fs
from datetime import datetime
from sqlalchemy import create_engine
from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets


load_dotenv()
connection_string = "postgresql+psycopg2://username:password@localhost/mydatabase"

# S3 config
BUCKET_NAME = "soda-dagster"
FILE_KEYS = [
    "bikes/brands.csv",
    "bikes/categories.csv",
    "bikes/customers.csv",
    "bikes/order_items.csv",
    "bikes/orders.csv",
    "bikes/products.csv",
    "bikes/staffs.csv",
    "bikes/stocks.csv",
    "bikes/stores.csv",
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
print(credentials)
print(encoded_credentials)
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
                get_dagster_logger().info(f"Scan: {state} successfully")

                break
            else:
                print(f"Scan failed with state: {state}")
                get_dagster_logger().info(f"Scan failed with status: {state}")
                raise Failure("Soda Cloud Check Failed")
                # exit(1)
        else:
            print(f"GET request failed with status code {get_response.status_code}")
            print(get_response.text)
            get_dagster_logger().info(
                f"GET request failed with status code {get_response.status_code}"
            )
            exit(1)


class CustomSampler(Sampler):
    def store_sample(self, sample_context: SampleContext):
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


@asset(compute_kind="python")
def ingestion_checks(context):

    s3 = boto3.client("s3")
    dataframes = {}

    for i, file_key in enumerate(FILE_KEYS, start=1):
        try:
            # Read file from S3
            response = s3.get_object(Bucket=BUCKET_NAME, Key=file_key)
            file_content = response["Body"]

            # Load CSV into DataFrame
            df = pd.read_csv(file_content)
            dataframes[i] = df
            print("loaded")
            get_dagster_logger().info(
                f"Successfully loaded DataFrame for {file_key} with {len(df)} rows."
            )

        except Exception as e:
            get_dagster_logger().error(f"Error loading {file_key}: {e}")
    failed_rows_cloud = "false"
    # Initialize Soda Scan
    scan = Scan()
    scan.set_scan_definition_name("Soda Dagster Demo")
    scan.set_data_source_name("soda-dagster")
    dataset_names = [
        "brands",
        "categories",
        "customers",
        "order_items",
        "orders",
        "products",
        "staffs",
        "stocks",
        "stores",
    ]

    # Add DataFrames to Soda Scan in a loop
    try:
        for i, dataset_name in enumerate(dataset_names, start=1):
            scan.add_pandas_dataframe(
                dataset_name=dataset_name,
                pandas_df=dataframes[i],
                data_source_name="soda-dagster",
            )
    except KeyError as e:
        get_dagster_logger().error(
            f"DataFrame missing for index {e}. Check if all files are loaded correctly."
        )

    config = f"""
  soda_cloud:
      host: demo.soda.io
      api_key_id: 951b6208-0aaa-49f4-b234-c5a3355e78ca
      api_key_secret: RQ1_zGxaKAev0osqS3hRtnFjvDkE2EOKgCfK40K0uzVipvc3B8arpw
  """

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
      name: Invalid row count
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
      valid format: email
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Validity
        data_domain: Location
        weight: 1
  - invalid_count(phone) = 0:
      valid format: phone number
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Validity
        data_domain: Location
        weight: 1

checks for stocks:
  - row_count > 0:
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Completeness
        data_domain: Product
        weight: 3
  - values in (store_id) must exist in stores (store_id):
      attributes:
        pipeline_stage: Pre-ingestion
        data_quality_dimension:
          - Consistency
        data_domain: Product
        weight: 2
  - values in (product_id) must exist in products (product_id):
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
    scan.add_sodacl_yaml_file
    scan.add_configuration_yaml_file
    scan.add_configuration_yaml_str(config)
    scan.add_sodacl_yaml_str(checks)
    # scan.add_variables({'DATE':'08/24'})
    if failed_rows_cloud == "false":
        scan.sampler = CustomSampler()
    scan.execute()
    logs = scan.get_logs_text()

    scan_results = scan.get_scan_results()
    context.log.info("Scan executed successfully.")
    # scan.sampler.store_sample()
    get_dagster_logger().info(scan_results)
    get_dagster_logger().info(logs)
    # scan.assert_no_checks_warn_or_fail()

    return Output(
        value=scan_results,  # The actual value
        metadata={
            "scan_results": MetadataValue.json(scan_results),
            "logs": MetadataValue.json(logs),  # Saving the results as JSON
        },
    )


@asset(deps=[ingestion_checks], compute_kind="python")
def load_from_s3(context):
    copy_data()


@dbt_assets(select="marts", manifest=dagsteretl_project.manifest_path)
def dbt_staging(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(
        ["build"], context=context, manifest=dagsteretl_project.manifest_path
    ).stream()


@asset(deps=[dbt_staging], compute_kind="python")
def soda_UI_check(context):
    trigger_scan()


@dbt_assets(select="prod", manifest=dagsteretl_project.manifest_path)
def dbt_prod(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(
        ["build"], context=context, manifest=dagsteretl_project.manifest_path
    ).stream()
