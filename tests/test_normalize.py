"""Tests for normalize.py — S6: parse_number, parse_date, parse_dims."""

import pytest

from ugr_booking.normalize import fold, parse_date, parse_dims, parse_number


# ===========================================================================
# fold() — regression guard (S2)
# ===========================================================================

class TestFold:
    def test_izmir(self):
        assert fold("\u0130ZM\u0130R") == "izmir"

    def test_sagunto(self):
        assert fold("S\u00c3GUNTO") == "sagunto"

    def test_none_returns_empty(self):
        assert fold(None) == ""

    def test_collapse_whitespace(self):
        assert fold("  foo   bar  ") == "foo bar"


# ===========================================================================
# parse_number()
# ===========================================================================

class TestParseNumber:
    def test_en_thousands_comma_weight(self):
        """12,400 -> 12400  (weight context, EN thousands separator)."""
        assert parse_number("12,400") == 12_400.0

    def test_eu_decimal_dot_dim(self):
        """2.450 -> 2.45  (dim context, EU decimal with trailing zero)."""
        assert parse_number("2.450", context="dim") == pytest.approx(2.45)

    def test_eu_mixed_thousands_and_decimal(self):
        """7.330,50 -> 7330.5  (EU thousands dot + EU decimal comma)."""
        assert parse_number("7.330,50") == pytest.approx(7330.5)

    def test_space_thousands_separator(self):
        """7 450 -> 7450  (space as thousands separator)."""
        assert parse_number("7 450") == 7_450.0

    def test_null_string_returns_none(self):
        assert parse_number("null") is None

    def test_none_returns_none(self):
        assert parse_number(None) is None

    def test_empty_string_returns_none(self):
        assert parse_number("") is None

    def test_plain_integer(self):
        assert parse_number("1500") == 1500.0

    def test_plain_float(self):
        assert parse_number("1500.5") == 1500.5

    def test_en_thousands_with_decimal(self):
        assert parse_number("12,400.50") == pytest.approx(12_400.5)

    def test_eu_decimal_comma_dim(self):
        """2,45 (no thousands) -> 2.45 in dim context."""
        assert parse_number("2,45", context="dim") == pytest.approx(2.45)

    def test_eu_thousands_dot_weight(self):
        """7.330 in weight context -> 7330."""
        assert parse_number("7.330", context="weight") == 7330.0

    def test_trailing_unit_stripped(self):
        assert parse_number("12,400 kg") == 12_400.0

    def test_nbsp_thousands_separator(self):
        assert parse_number("7\u00a0450") == 7_450.0


# ===========================================================================
# parse_date()
# ===========================================================================

class TestParseDate:
    def test_invalid_date_feb_31(self):
        assert parse_date("31/02/2026") == "INVALID_DATE"

    def test_two_digit_year(self):
        assert parse_date("27.11.26") == "2026-11-27"

    def test_null_string_returns_none(self):
        assert parse_date("null") is None

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_iso_format(self):
        assert parse_date("2026-09-14") == "2026-09-14"

    def test_iso_with_slash(self):
        assert parse_date("2026/09/14") == "2026-09-14"

    def test_dmy_slash(self):
        assert parse_date("14/09/2026") == "2026-09-14"

    def test_dmy_dot(self):
        assert parse_date("14.09.2026") == "2026-09-14"

    def test_dmy_dash(self):
        assert parse_date("14-09-2026") == "2026-09-14"

    def test_single_digit_day_month(self):
        assert parse_date("1/9/2026") == "2026-09-01"

    def test_2digit_year_49_maps_to_2049(self):
        assert parse_date("01.01.49") == "2049-01-01"

    def test_2digit_year_50_maps_to_1950(self):
        assert parse_date("01.01.50") == "1950-01-01"

    def test_empty_string_returns_none(self):
        assert parse_date("") is None

    def test_invalid_month_13(self):
        assert parse_date("01/13/2026") == "INVALID_DATE"

    def test_invalid_day_0(self):
        assert parse_date("00/01/2026") == "INVALID_DATE"


# ===========================================================================
# parse_dims()
# ===========================================================================

class TestParseDims:
    def test_cm_to_m(self):
        assert parse_dims("620 cm") == pytest.approx(6.2)

    def test_mm_to_m(self):
        assert parse_dims("6200 mm") == pytest.approx(6.2)

    def test_metres_passthrough(self):
        assert parse_dims("6.20 m") == pytest.approx(6.2)

    def test_no_unit_assumed_metres(self):
        assert parse_dims("6.2") == pytest.approx(6.2)

    def test_null_string_returns_none(self):
        assert parse_dims("null") is None

    def test_none_returns_none(self):
        assert parse_dims(None) is None

    def test_empty_returns_none(self):
        assert parse_dims("") is None

    def test_cm_uppercase(self):
        assert parse_dims("620 CM") == pytest.approx(6.2)

    def test_mm_uppercase(self):
        assert parse_dims("6200 MM") == pytest.approx(6.2)

    def test_eu_decimal_cm(self):
        assert parse_dims("620,00 cm") == pytest.approx(6.2)
