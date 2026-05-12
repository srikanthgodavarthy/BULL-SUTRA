"""
data/oi.py — NSE Option Chain / Open Interest data via NSE API.
"""

import time
import requests
import pandas as pd
import streamlit as st

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Connection":       "keep-alive",
}


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def _warm_session(session: requests.Session) -> bool:
    try:
        session.get("https://www.nseindia.com", timeout=10)
        time.sleep(0.8)
        session.get(
            "https://www.nseindia.com/market-data/equity-derivatives-watch",
            timeout=10,
        )
        time.sleep(0.5)
        return True
    except Exception:
        return False


@st.cache_data(ttl=180)
def fetch_oi_data(symbol: str = "NIFTY") -> dict | None:
    session = _make_session()
    _warm_session(session)

    oc_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    data   = None

    for attempt in range(3):
        try:
            resp = session.get(oc_url, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                break
            elif resp.status_code in (401, 403):
                _warm_session(session)
        except Exception:
            pass
        time.sleep(1.5 ** attempt)

    if data is None:
        return {
            "symbol": symbol,
            "expiry": None,
            "spot": 0,
            "pcr": 1.0,
            "max_pain": None,
            "call_wall": None,
            "put_wall": None,
            "top_ce": [],
            "top_pe": [],
            "df_oi": pd.DataFrame(),
            "status": "unavailable"
        }

    try:
        records       = data["records"]
        spot          = float(records["underlyingValue"])
        expiries      = records["expiryDates"]
        weekly_expiry = expiries[0] if expiries else None

        rows = []
        for item in records["data"]:
            if item.get("expiryDate") != weekly_expiry:
                continue
            strike = item["strikePrice"]
            ce_oi  = item.get("CE", {}).get("openInterest", 0) or 0
            pe_oi  = item.get("PE", {}).get("openInterest", 0) or 0
            ce_chg = item.get("CE", {}).get("changeinOpenInterest", 0) or 0
            pe_chg = item.get("PE", {}).get("changeinOpenInterest", 0) or 0
            rows.append({"Strike": strike, "CE_OI": ce_oi, "CE_Chg": ce_chg,
                         "PE_OI": pe_oi, "PE_Chg": pe_chg})

        if not rows:
            return None

        df_oi    = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)
        total_ce = df_oi["CE_OI"].sum()
        total_pe = df_oi["PE_OI"].sum()
        pcr      = round(total_pe / total_ce, 2) if total_ce > 0 else 0

        pains = []
        for s in df_oi["Strike"]:
            ce_l = ((df_oi["Strike"] - s).clip(lower=0) * df_oi["CE_OI"]).sum()
            pe_l = ((s - df_oi["Strike"]).clip(lower=0) * df_oi["PE_OI"]).sum()
            pains.append(ce_l + pe_l)
        df_oi["TotalPain"] = pains

        return {
            "symbol":    symbol,
            "expiry":    weekly_expiry,
            "spot":      spot,
            "pcr":       pcr,
            "max_pain":  int(df_oi.loc[df_oi["TotalPain"].idxmin(), "Strike"]),
            "call_wall": int(df_oi.loc[df_oi["CE_OI"].idxmax(), "Strike"]),
            "put_wall":  int(df_oi.loc[df_oi["PE_OI"].idxmax(), "Strike"]),
            "top_ce":    df_oi.nlargest(5, "CE_OI")[["Strike", "CE_OI", "CE_Chg"]].to_dict("records"),
            "top_pe":    df_oi.nlargest(5, "PE_OI")[["Strike", "PE_OI", "PE_Chg"]].to_dict("records"),
            "df_oi":     df_oi,
        }
    except Exception as e:
        return {
            "symbol": symbol,
            "expiry": None,
            "spot": 0,
            "pcr": 1.0,
            "max_pain": None,
            "call_wall": None,
            "put_wall": None,
            "top_ce": [],
            "top_pe": [],
            "df_oi": pd.DataFrame(),
            "status": f"error: {str(e)}"
        }


def oi_sentiment(pcr: float) -> tuple[str, str]:
    if pcr >= 1.3: return "Bullish", "#16a34a"
    if pcr >= 0.9: return "Neutral", "#d97706"
    return "Bearish", "#dc2626"
