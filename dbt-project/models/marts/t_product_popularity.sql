{{ config(
    materialized='table',
    schema='staging'
) }}

with product_sales as (
    select
        p.product_id,
        p.product_name,
        p.category_id,
        p.brand_id,
        sum(o.quantity) as total_quantity_sold,
        sum(o.list_price * o.quantity) as total_sales
    from {{source('dev', 'order_items')}} o
    join dev.demo.products p
    on o.product_id = p.product_id
    group by p.product_id, p.product_name, p.category_id, p.brand_id
),
fake_products as (
    select
        -1 as product_id, -- Use negative or distinct IDs to avoid conflicts
        'Cowboy Cruiser' as product_name,
        999 as category_id, -- Assign a distinct or placeholder category ID
        999 as brand_id,    -- Assign a distinct or placeholder brand ID
        -10 as total_quantity_sold, -- Negative quantity
        -500 as total_sales         -- Negative sales
    union all
    select
        -2 as product_id,
        'Cowboy Cross' as product_name,
        999 as category_id,
        999 as brand_id,
        -20 as total_quantity_sold,
        -1000 as total_sales
)
select
    product_id,
    product_name,
    category_id,
    brand_id,
    total_quantity_sold,
    total_sales
from product_sales

union all

select
    product_id,
    product_name,
    category_id,
    brand_id,
    total_quantity_sold,
    total_sales
from fake_products

order by total_sales desc
