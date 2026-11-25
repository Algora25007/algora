from datetime import date

import pytest

from algora_engine import calendar as cal
from algora_engine.instruments import Segment


def test_holiday_data_loaded():
    assert cal.holiday_name(date(2026, 1, 26)) == "Republic Day"
    assert cal.holiday_name(date(2026, 1, 15)) == "Municipal elections"  # NSE/CMTR/72260
    assert len(cal.load_holidays().nse) == 16
    assert cal.is_known_year(2026) and not cal.is_known_year(2027)


def test_trading_days():
    assert not cal.is_nse_trading_day(date(2026, 11, 28))  # Saturday
    assert not cal.is_nse_trading_day(date(2026, 11, 24))  # Guru Nanak Jayanti
    assert cal.is_nse_trading_day(date(2026, 11, 25))
    assert cal.prev_trading_day(date(2026, 11, 24)) == date(2026, 11, 23)
    assert cal.prev_trading_day(date(2026, 4, 5)) == date(2026, 4, 2)  # Sun -> Sat -> Good Friday -> Thu
    assert cal.next_trading_day(date(2026, 4, 2)) == date(2026, 4, 6)
    # Unknown year: weekdays treated as open.
    assert cal.is_nse_trading_day(date(2027, 1, 26))


@pytest.mark.parametrize(
    ("d", "expected"),
    [
        (date(2026, 3, 8), False),  # 2nd Sunday of March itself counts as standard (as in the front end)
        (date(2026, 3, 9), True),
        (date(2026, 3, 6), False),
        (date(2026, 10, 30), True),
        (date(2026, 11, 1), True),  # 1st Sunday of November
        (date(2026, 11, 2), False),
        (date(2026, 6, 3), True),
        (date(2026, 12, 1), False),
    ],
)
def test_us_dst(d, expected):
    assert cal.us_dst(d) is expected


def test_mcx_close_follows_us_dst():
    assert cal.mcx_close(date(2026, 6, 3)) == 23 * 60 + 30
    assert cal.mcx_close(date(2026, 12, 1)) == 23 * 60 + 55


def test_expiries_shift_to_previous_trading_day():
    # Tue 24 Nov 2026 is a holiday and the last Tuesday: the monthly Nifty expiry moves to Mon 23 Nov.
    assert date(2026, 11, 24).weekday() == 1
    assert cal.expiries_for(date(2026, 11, 23)) == [cal.Expiry("NSE", "NIFTY", True)]
    assert cal.expiries_for(date(2026, 11, 24)) == []
    # Holi Tue 3 Mar -> weekly expiry Mon 2 Mar; Mahavir Jayanti Tue 31 Mar (last Tuesday) -> Mon 30 Mar monthly.
    assert cal.expiries_for(date(2026, 3, 2)) == [cal.Expiry("NSE", "NIFTY", False)]
    assert cal.expiries_for(date(2026, 3, 30)) == [cal.Expiry("NSE", "NIFTY", True)]
    # Ram Navami Thu 26 Mar (the last Thursday) -> Sensex monthly Wed 25 Mar.
    assert cal.expiries_for(date(2026, 3, 25)) == [cal.Expiry("BSE", "SENSEX", True)]
    assert cal.expiries_for(date(2026, 3, 19)) == [cal.Expiry("BSE", "SENSEX", False)]
    # Normal Thursday, last of the month -> Sensex monthly.
    assert cal.expiries_for(date(2026, 11, 26)) == [cal.Expiry("BSE", "SENSEX", True)]
    assert cal.expiries_for(date(2026, 12, 8)) == [cal.Expiry("NSE", "NIFTY", False)]


def test_next_expiry():
    assert cal.next_expiry("NIFTY", date(2026, 11, 20)) == (date(2026, 11, 23), True)
    assert cal.next_expiry("NIFTY", date(2026, 11, 23), now_minute=9 * 60) == (date(2026, 11, 23), True)
    assert cal.next_expiry("NIFTY", date(2026, 11, 23), now_minute=16 * 60) == (date(2026, 12, 1), False)
    assert cal.next_expiry("SENSEX", date(2026, 11, 27)) == (date(2026, 12, 3), False)


def test_sessions_before_and_after_fno_timing_change():
    fri = cal.sessions(date(2026, 12, 4))
    assert fri.fno == (555, 15 * 60 + 40) and fri.fno_expiring_indices == ()
    assert fri.nse_cash == (555, 930) and fri.nse_pre_open == (540, 555)
    mon = cal.sessions(date(2026, 12, 7))
    assert mon.fno == (555, 15 * 60 + 45)
    tue = cal.sessions(date(2026, 12, 8))
    assert tue.fno_expiring_indices == ("NIFTY",)
    # Before 7 Dec an expiry day lists no 15:30 stop.
    assert cal.sessions(date(2026, 12, 1)).fno_expiring_indices == ()


def test_sessions_on_holidays_and_weekends():
    hol = cal.sessions(date(2026, 11, 10))
    assert hol.holiday == "Diwali Balipratipada" and hol.nse_cash is None and hol.fno is None
    assert hol.mcx == (17 * 60, 23 * 60 + 55) and hol.mcx_evening_only
    xmas = cal.sessions(date(2026, 12, 25))
    assert xmas.mcx is None
    wk = cal.sessions(date(2026, 11, 28))
    assert wk.weekend and wk.mcx is None and wk.nse_cash is None
    assert not cal.sessions(date(2027, 3, 1)).known_year


def test_market_close_by_segment():
    expiry = date(2026, 12, 8)
    assert cal.market_close(expiry, Segment.OPTIONS, "NIFTY") == 15 * 60 + 30
    assert cal.market_close(expiry, Segment.FUTURES, "NIFTY") == 15 * 60 + 45  # weekly options only
    assert cal.market_close(expiry, Segment.OPTIONS, "BANKNIFTY") == 15 * 60 + 45
    assert cal.market_close(date(2026, 12, 29), Segment.FUTURES, "BANKNIFTY") == 15 * 60 + 30  # monthly
    assert cal.market_close(date(2026, 12, 10), Segment.OPTIONS, "SENSEX") == 15 * 60 + 30
    assert cal.market_close(date(2026, 12, 8), Segment.OPTIONS, "RELIANCE") == 15 * 60 + 45
    assert cal.market_close(date(2026, 12, 1), Segment.OPTIONS, "NIFTY") == 15 * 60 + 40  # before 7 Dec
    assert cal.market_close(expiry, Segment.EQUITY_INTRADAY, "INFY") == 930
    assert cal.market_close(expiry, Segment.COMMODITY_FUTURES, "CRUDEOIL") == 23 * 60 + 55
    assert cal.market_close(date(2026, 11, 24), Segment.FUTURES, "NIFTY") is None
    assert cal.market_close(date(2026, 12, 25), Segment.COMMODITY_OPTIONS, "GOLDM") is None


def test_is_contract_expiry_day():
    assert cal.is_contract_expiry_day("NIFTY", Segment.OPTIONS, date(2026, 12, 8))
    assert not cal.is_contract_expiry_day("NIFTY", Segment.FUTURES, date(2026, 12, 8))
    assert cal.is_contract_expiry_day("NIFTY", Segment.FUTURES, date(2026, 12, 29))
    assert not cal.is_contract_expiry_day("NIFTY", Segment.EQUITY_INTRADAY, date(2026, 12, 29))
    assert not cal.is_contract_expiry_day("TCS", Segment.FUTURES, date(2026, 12, 29))
    assert cal.is_contract_expiry_day("BANKEX", Segment.FUTURES, date(2026, 12, 31))
    assert not cal.is_contract_expiry_day("BANKEX", Segment.OPTIONS, date(2026, 12, 10))
