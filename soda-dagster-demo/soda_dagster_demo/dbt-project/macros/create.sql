-- macros/create_retail_country_codes_table.sql

{% macro create_retail_country_codes_table() %}
    CREATE TABLE IF NOT EXISTS {{ this.schema }}.retail_country_codes (
        id INT,
        iso TEXT,
        name TEXT,
        nicename TEXT,
        iso3 TEXT,
        numcode INT,
        phonecode INT
    );
{% endmacro %}
