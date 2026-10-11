from marketdata.symbol import Market, Symbol


def test_parse_detects_market():
    assert Symbol.parse("600519").market == Market.CN
    assert Symbol.parse("000001").market == Market.CN
    assert Symbol.parse("00700").market == Market.HK
    assert Symbol.parse("AAPL").market == Market.US
    assert Symbol.parse("2330").market == Market.TW
    assert Symbol.parse("0050").market == Market.TW
    assert Symbol.parse("2881A").market == Market.TW
    assert Symbol.parse("2330.TW").market == Market.TW
    assert Symbol.parse("2330.TW").code == "2330"
    assert Symbol.parse("6547.TWO").market == Market.TW
    assert Symbol.parse("6547.TWO").code == "6547"


def test_parse_respects_explicit_market():
    assert Symbol.parse("00700", "HK").market == Market.HK
    assert Symbol.parse("600519", "CN").code == "600519"
    assert Symbol.parse("2330", "TW").market == Market.TW


def test_to_tencent():
    assert Symbol.parse("600519").to_tencent() == "sh600519"
    assert Symbol.parse("000001").to_tencent() == "sz000001"
    assert Symbol.parse("920001").to_tencent() == "bj920001"
    assert Symbol.parse("00700", "HK").to_tencent() == "hk00700"
    assert Symbol.parse("AAPL").to_tencent() == "usAAPL"


def test_to_yfinance():
    assert Symbol.parse("00700", "HK").to_yfinance() == "0700.HK"
    assert Symbol.parse("AAPL").to_yfinance() == "AAPL"
    assert Symbol.parse("2330").to_yfinance() == "2330.TW"
    assert Symbol.parse("6547", "TW").to_yfinance() == "6547.TW"
