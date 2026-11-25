from datetime import datetime

import pytest

from algora_engine.candles import CandleFileError, detect_timeframe, parse_csv, parse_when


@pytest.mark.parametrize(
    ("s", "expected"),
    [
        ("2026-06-03 09:15", datetime(2026, 6, 3, 9, 15)),
        ("2026/06/03T09:15:00", datetime(2026, 6, 3, 9, 15)),
        ("2026-06-03 9:15 am", datetime(2026, 6, 3, 9, 15)),
        ("2026-06-03 3:15 PM", datetime(2026, 6, 3, 15, 15)),
        ("2026-06-03 12:05 am", datetime(2026, 6, 3, 0, 5)),
        ("03-06-2026 09:15", datetime(2026, 6, 3, 9, 15)),
        ("03/06/2026 3:30 pm", datetime(2026, 6, 3, 15, 30)),
        ("03.06.2026", datetime(2026, 6, 3)),
        ("2026-06-03T03:45:00Z", datetime(2026, 6, 3, 9, 15)),  # UTC -> IST
        ("2026-06-03T09:15:00+05:30", datetime(2026, 6, 3, 9, 15)),
        ("2026-06-03T04:45:00+0100", datetime(2026, 6, 3, 9, 15)),
        ("1780458300", datetime(2026, 6, 3, 9, 15)),  # epoch seconds (UTC 03:45)
        ("1780458300000", datetime(2026, 6, 3, 9, 15)),  # epoch milliseconds
    ],
)
def test_parse_when(s, expected):
    assert parse_when(s) == expected


@pytest.mark.parametrize("s", ["", "yesterday", "2026-13-01 09:15", "2026-02-30", "2026-06-03 13:15 pm", "31-31-2026"])
def test_parse_when_rejects(s):
    assert parse_when(s) is None


CSV = """\ufeffDate,Open,High,Low,Close,Volume
2026-06-03 09:30,101,102.5,100.5,102,10
2026-06-03 09:15,100,102,99,101,20
2026-06-03 09:15,100,102,99,101,20
2026-06-03 09:45,102,104,101.5,103.5,
bad,1,2,3,4,5
2026-06-03 10:00,104,103,103,104.5,5
2026-06-03 10:15,104.5,106,104,x,5
2026-06-03 10:30,0,1,0,1,1
"""


def test_parse_csv_sorts_dedupes_and_counts():
    r = parse_csv(CSV)
    assert [b.ts.strftime("%H:%M") for b in r.bars] == ["09:15", "09:30", "09:45"]
    assert r.bars[0].volume == 20 and r.bars[2].volume is None
    assert r.skipped_duplicate == 1
    assert r.skipped_unreadable == 2  # "bad" datetime, "x" close
    assert r.skipped_invalid == 2  # high below open, zero prices
    assert r.skipped == 5
    assert r.timeframe_minutes == 15 and r.sessions == 1


def test_parse_csv_separate_date_time_columns_and_semicolons():
    text = "date;time;open;high;low;close\n03-06-2026;09:15;1;2;0.5;1.5\n03-06-2026;09:20;1.5;2;1;1.8\n"
    r = parse_csv(text)
    assert r.bars[1].ts == datetime(2026, 6, 3, 9, 20) and r.timeframe_minutes == 5


def test_parse_csv_tabs():
    r = parse_csv("timestamp\topen\thigh\tlow\tclose\n1780458300\t1\t2\t0.5\t1.5\n")
    assert r.bars[0].ts == datetime(2026, 6, 3, 9, 15)
    assert r.timeframe_minutes == 0  # a single candle: no intraday gap


def test_parse_csv_errors():
    with pytest.raises(CandleFileError, match="no candle rows"):
        parse_csv("datetime,open,high,low,close\n")
    with pytest.raises(CandleFileError, match="Missing columns: datetime, close"):
        parse_csv("when,open,high,low\n1,1,1,1\n")
    with pytest.raises(CandleFileError, match="No valid candle rows"):
        parse_csv("datetime,open,high,low,close\nnope,1,1,1,1\n")
    with pytest.raises(CandleFileError, match="more than"):
        parse_csv("datetime,open,high,low,close\n" + "2026-06-03 09:15,1,1,1,1\n" * 3, max_rows=2)


def test_detect_timeframe_daily_and_ties():
    daily = parse_csv("date,open,high,low,close\n2026-06-01,1,2,0.5,1\n2026-06-02,1,2,0.5,1\n")
    assert daily.timeframe_minutes == 0
    tie = parse_csv(
        "datetime,open,high,low,close\n2026-06-01 09:15,1,2,0.5,1\n2026-06-01 09:20,1,2,0.5,1\n"
        "2026-06-01 09:30,1,2,0.5,1\n"
    )
    assert detect_timeframe(tie.bars) == 5
