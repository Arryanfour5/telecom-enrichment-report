"""The financial / e-commerce SMS blocks pick senders by words in dim_sms_sender.sub_category and show it. No database."""
from config.report_mapping import (TelecomReportMapping as M, _ECOMMERCE_KEYWORDS, _FINANCIAL_KEYWORDS, _sql_any_word)

# every sub_category in use: the API's keyword taxonomy, then the TRAI header list's sectors (23 Major-Categories)
KNOWN = ["Government", "Banking", "Payments/Fintech", "Lending/Credit", "Insurance", "E-commerce", "Food Delivery", "Travel/Transport",
         "Healthcare", "Telecom", "Utility/Energy", "Entertainment/OTT", "Education", "Real Estate", "Testing/Internal", "Unknown",
         "Inmarsat", "Thuraya", "International satellite",
         "Professional & Business Services", "Technology & Digital Services", "Banking & Finance", "Education & Training",
         "Retail & Consumer Services", "Media & Entertainment", "Government & Public Sector", "Healthcare & Life Sciences",
         "Travel & Hospitality", "Agriculture & Food", "Associations & Civil Society", "Real Estate & Construction",
         "Healthcare & Wellness", "Government & Public Services", "Automotive", "Automotive & Mobility", "Manufacturing & Industry",
         "E-Commerce & Retail", "Telecommunications", "Logistics & Transportation", "Utilities & Environment", "Politics & Civic Affairs"]


def _hits(words):
    return {k for k in KNOWN if any(w in k.lower() for w in words)}


def test_financial_words_catch_the_financial_sub_categories_and_nothing_else():
    assert _hits(_FINANCIAL_KEYWORDS) == {"Banking", "Payments/Fintech", "Lending/Credit", "Insurance", "Banking & Finance"}


def test_ecommerce_words_catch_the_ecommerce_sub_categories_and_not_agriculture_and_food():
    assert _hits(_ECOMMERCE_KEYWORDS) == {"E-commerce", "Food Delivery", "Retail & Consumer Services", "E-Commerce & Retail"}


def test_a_new_sub_category_is_picked_up_by_its_words():
    assert {"Digital Payments", "Financial Services", "Loans"} <= _hits_of(["Digital Payments", "Financial Services", "Loans"], _FINANCIAL_KEYWORDS)
    assert {"Online Shopping", "Marketplace"} <= _hits_of(["Online Shopping", "Marketplace"], _ECOMMERCE_KEYWORDS)


def _hits_of(names, words):
    return {k for k in names if any(w in k.lower() for w in words)}


def test_each_block_filters_on_its_words_and_returns_the_sub_category():
    for fn, words in ((M.financial_sms, _FINANCIAL_KEYWORDS), (M.ecommerce_sms, _ECOMMERCE_KEYWORDS)):
        sql = fn()["clickhouse_query"]
        assert "AND " + _sql_any_word("s.sub_category", words) in sql
        assert "s.sub_category   AS sub_category" in sql and "GROUP BY sender, sender_id, category, sub_category" in sql
        assert "s.categorization = " not in sql                  # the DLT type is shown, no longer the filter


def test_the_join_reads_the_current_version_of_each_sender():
    for fn in (M.financial_sms, M.ecommerce_sms):
        assert "master.dim_sms_sender AS s FINAL ON" in fn()["clickhouse_query"]
