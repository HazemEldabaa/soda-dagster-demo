{{ config(
    materialized='table',
    schema= 'staging'
) }}

with product_sales as (
    select
        p.product_id,
        p.product_name,
        p.category_id,
        p.brand_id,
        sum(o.quantity) as total_quantity_sold,
        sum(o.list_price * o.quantity) as total_sales
    --from dev.demo.order_items o
    from {{source('dev','order_items')}} o
    join dev.demo.products p
    on o.product_id = p.product_id
    group by p.product_id, p.product_name, p.category_id, p.brand_id
)
select
    product_id,
    product_name,
    category_id,
    brand_id,
    total_quantity_sold,
    total_sales
from product_sales
order by total_sales desc
