from datetime import datetime, timedelta, timezone

import pandas as pd

from smartcapital.market import _completed_sessions


def _df(dates, closes):
    idx = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in dates])
    return pd.DataFrame({"close": closes}, index=idx)


def test_drops_todays_forming_bar():
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    two_ago = today - timedelta(days=2)
    df = _df([two_ago, yesterday, today], [208.76, 206.84, 195.75])
    out = _completed_sessions(df)
    # Today's partial bar is gone; previous close is the real prior session.
    assert out.index[-1].date() == yesterday
    assert float(out["close"].iloc[-1]) == 206.84


def test_keeps_all_when_no_bar_for_today():
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    two_ago = today - timedelta(days=2)
    df = _df([two_ago, yesterday], [208.76, 206.84])
    out = _completed_sessions(df)
    assert len(out) == 2


def test_empty_passthrough():
    df = pd.DataFrame({"close": []})
    assert _completed_sessions(df).empty
