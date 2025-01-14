from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets, get_asset_key_for_source, get_asset_keys_by_output_name_for_source
from io import StringIO
import yaml
import boto3
import pandas as pd
from dagster import asset, asset_check, materialize, Output, Definitions, get_dagster_logger, job, MetadataValue, Failure, AssetExecutionContext, AssetCheckResult, AssetCheckSpec, AssetKey, multi_asset_check, AssetMaterialization
from dagster_aws.s3 import S3Resource
from soda.sampler.sampler import Sampler
from soda.sampler.sample_context import SampleContext
import psycopg2
from psycopg2 import sql
from soda.scan import Scan
from dotenv import load_dotenv
import base64
import subprocess
import os
import re
import requests
import json
import time
import datetime
import s3fs
from datetime import datetime
from sqlalchemy import create_engine
from dagster import AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets
import sys

load_dotenv()

api_key_id = os.getenv('soda_api_key_id')
api_key_secret = os.getenv('soda_api_key_secret')

def clean_name(name):
    return re.sub(r'\W+', '_', name)
def get_ids(input_table_names, input_datasources):
    """
    This function fetches datasets from Soda Cloud and returns only the datasets
    that match the input table names and datasources.
    
    :param input_table_names: List of table names you want to extract.
    :param input_datasources: List of datasource names to filter datasets.
    :return: List of dictionaries with matching table names, datasources, and cloud URLs.
    """
    load_dotenv()

    # Soda Cloud Instance
    soda_cloud_url = "https://demo.soda.io"  # Your Soda Cloud URL
    soda_apikey = os.getenv("soda_api_key_id")  # User API key ID from Soda Cloud
    soda_apikey_secret = os.getenv("soda_api_key_secret")  # User API key secret from Soda Cloud

    # Initialize list to store matching dataset names and cloud URLs
    dataset_ids = []

    # Fetch datasets and iterate over all pages to get cloudUrl for each dataset
    response_datasets = requests.get(
        soda_cloud_url + "/api/v1/datasets?page=0",
        auth=(soda_apikey, soda_apikey_secret),
    )

    if response_datasets.status_code in [401, 403]:
        get_dagster_logger().info("Unauthorized or Forbidden access. Please check your API keys and/or permissions in Soda.")
        sys.exit()

    if response_datasets.status_code == 200:
        dataset_pages = response_datasets.json().get("totalPages", 1)

        i = 0
        while i < dataset_pages:
            dq_datasets = requests.get(
                soda_cloud_url + "/api/v1/datasets?page=" + str(i),
                auth=(soda_apikey, soda_apikey_secret),
            )

            if dq_datasets.status_code == 200:
                print(f"Fetching all datasets on page: {i}")
                get_dagster_logger().info(f"Fetching all datasets on page: {i}")
                page_data = dq_datasets.json().get("content", [])
                
                # Collect dataset names and cloud URLs only if they match the input table names and datasources
                for dataset in page_data:
                    dataset_name = dataset.get("name")
                    datasource_name = dataset.get("datasource", {}).get("name")
                    dataset_id = dataset.get("id")

                    if dataset_name in input_table_names and datasource_name in input_datasources:
                        dataset_ids.append(dataset_id)
                
                i += 1
            elif dq_datasets.status_code == 429:
                print(f"API Rate Limit reached when fetching datasets on page: {i}. Pausing for 30 seconds.")
                time.sleep(30)
            else:
                print(f"Error fetching datasets on page {i}. Status code: {dq_datasets.status_code}")
                sys.exit()
    else:
        print(f"Error fetching initial datasets. Status code: {response_datasets.status_code}")
        sys.exit()

    # Return the list of matching table names, datasources, and their corresponding cloud URLs
    return dataset_ids
def read_s3(file_key):
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket='soda-dagster', Key=file_key)
    file_content = response['Body']

    # Load CSV into DataFrame
    df = pd.read_csv(file_content)
    return df

def checks_api(dataset_ids, dbt=False, ingestion=False, max_retries=5):    
    # Create lists to hold dynamically created check specs and responses
    check_specs = []
    check_response = []

    for dataset in dataset_ids:
        retries = 0
        while retries <= max_retries:
            try:
                response = requests.get(url=f'https://demo.soda.io/api/v1/checks?datasetId={dataset}', auth=(api_key_id,api_key_secret))

                # Handle rate-limiting (429 Too Many Requests)
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 30))  # Use 'Retry-After' or default to 5 seconds
                    print(f"Rate-limited. Retrying after {retry_after} seconds...")
                    time.sleep(retry_after)
                    retries += 1
                    continue

                # Raise an exception for other non-200 status codes
                response.raise_for_status()

                # Parse the JSON data
                json_data = response.json()

                # Iterate over the checks from the JSON data
                for check in json_data['content']:
                    check_name = check.get('name', 'Unnamed Check')
                    check_status = check.get('evaluationStatus')
                    dataset_name = check['datasets'][0]['name'] if check['datasets'] else 'Unknown Dataset'
                    cloud_url = check.get('cloudUrl')

                    # Create an AssetCheckSpec for the check
                    if dbt:
                        check_specs.append(
                            AssetCheckSpec(
                                name=clean_name(check_name), 
                                asset=AssetKey(['staging', f'{dataset_name}'])
                            )
                        )
                    elif ingestion:
                        check_specs.append(
                            AssetCheckSpec(
                                name=clean_name(check_name), 
                                asset=AssetKey(['s3_files'])
                            )
                        )
                    else:
                        check_specs.append(
                            AssetCheckSpec(
                                name=clean_name(check_name), 
                                asset=AssetKey(['load_from_s3'])
                            )
                        )

                    check_response.append((check_name, check_status, cloud_url))

                # Break out of retry loop on successful request
                break

            except requests.exceptions.RequestException as e:
                # Log and retry on exception
                print(f"Request failed: {e}. Retrying ({retries}/{max_retries})...")
                retries += 1
                time.sleep(5)  # Default delay between retries

        # If retries exceeded, log and raise an error
        if retries > max_retries:
            raise RuntimeError(f"Max retries exceeded for dataset {dataset}. Unable to fetch checks.")

    return check_specs, check_response



def trigger_scan():
    url = "https://demo.soda.io/api/v1/scans"
    payload = {"scanDefinition": "dagsterredshift_default_scan"}

    response = requests.post(url, data=payload, auth=(api_key_id,api_key_secret))

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
        get_response = requests.get(f"{url}/{scan_id}", auth=(api_key_id,api_key_secret))

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