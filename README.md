-- =============================================================
-- DB Strategy Weekly — Ultimate Metric Store
-- Separate from daily dashboard tables. Do not connect this to
-- market_indices_history or any daily dashboard process.
-- =============================================================

create table if not exists bank_metric_values (
  id bigint generated always as identity primary key,

  bank_name text not null,
  bank_ticker text,
  country text default 'Qatar',

  period_end date not null,
  period_type text not null check (period_type in ('quarter','year','month','day')),
  fiscal_year int not null,
  fiscal_quarter int check (fiscal_quarter between 1 and 4),

  metric_code text not null,
  metric_name text not null,
  metric_category text,
  value numeric,
  unit text default 'QAR million',
  currency text default 'QAR',

  source_type text default 'manual_verified',
  source_url text,
  source_note text,
  is_verified boolean default false,

  created_at timestamptz default now(),
  updated_at timestamptz default now(),

  unique (bank_name, period_end, period_type, metric_code)
);

create index if not exists idx_bmv_bank_period
  on bank_metric_values (bank_name, period_end desc);

create index if not exists idx_bmv_metric_period
  on bank_metric_values (metric_code, period_end desc);

create or replace view bank_metric_latest_verified as
select distinct on (bank_name, metric_code)
  *
from bank_metric_values
where is_verified = true
order by bank_name, metric_code, period_end desc;

create or replace view bank_metric_quarterly_changes as
with base as (
  select
    b.*,
    lag(value) over (
      partition by bank_name, metric_code, fiscal_quarter
      order by period_end
    ) as value_prior_year,
    lag(value) over (
      partition by bank_name, metric_code
      order by period_end
    ) as value_prior_quarter
  from bank_metric_values b
  where period_type = 'quarter'
    and is_verified = true
)
select
  *,
  round(100.0 * (value - value_prior_year) / nullif(value_prior_year, 0), 1) as yoy_pct,
  round(100.0 * (value - value_prior_quarter) / nullif(value_prior_quarter, 0), 1) as qoq_pct
from base;

-- Optional seed rows. Replace with your verified values.
insert into bank_metric_values
(bank_name, bank_ticker, country, period_end, period_type, fiscal_year, fiscal_quarter,
 metric_code, metric_name, metric_category, value, unit, currency, source_type, source_note, is_verified)
values
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'net_profit','Net Profit','profitability',234.4,'QAR million','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'total_assets','Total Assets','balance_sheet',121200,'QAR million','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'net_loans','Net Loans','balance_sheet',70500,'QAR million','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'customer_deposits','Customer Deposits','balance_sheet',56600,'QAR million','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'investment_portfolio','Investment Portfolio','balance_sheet',35100,'QAR million','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'nim_pct','Net Interest Margin','profitability',2.55,'percent','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'cet1_pct','CET1 Ratio','capital',12.06,'percent','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'car_pct','Capital Adequacy Ratio','capital',17.86,'percent','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'lcr_pct','Liquidity Coverage Ratio','liquidity',138,'percent','QAR','manual_verified','Q1 2026 reported result',true),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'net_gap_12m','Net 12M Repricing Gap','rate_sensitivity',8400,'QAR million','QAR','internal_alco','Illustrative. Replace with verified ALCO repricing report.',false),
('Doha Bank','QSE:DHBK','Qatar','2026-03-31','quarter',2026,1,'nii_at_risk_50bp','NII at Risk per 50bp','rate_sensitivity',42,'QAR million','QAR','internal_alco','Illustrative. Replace with verified ALCO repricing report.',false)
on conflict (bank_name, period_end, period_type, metric_code) do nothing;
