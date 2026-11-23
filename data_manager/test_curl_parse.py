"""parse_cookies_from_curl reads the login from every browser's "Copy as cURL".

The cookie values here are made up; only the SHAPE of each browser's output
matters. Chrome-family browsers write `-b '...'` (Windows cmd form with ^
escapes, or bash form); Safari and Firefox write `-H 'Cookie: ...'`.
Run: py data_manager/test_curl_parse.py   (or pytest)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from downloader import parse_cookies_from_curl  # noqa: E402

COOKIES = "lang=en_GB; accessToken=aaa.bbb.ccc; auth-ns:session=eyJzZXNz==; auth-ns:session.sig=SIG123"
WANT = {"accessToken": "aaa.bbb.ccc", "auth-ns:session": "eyJzZXNz==", "auth-ns:session.sig": "SIG123"}
URL = "https://cbssports.opta.trumediasports.com/dp-proxy"

SHAPES = {
    "chrome_windows_cmd": (f'curl ^"{URL}^" ^\n  -H ^"accept: application/json^" ^\n'
                           f'  -b ^"{COOKIES}^" ^\n  --data-raw ^"^{{^}}^"'),
    "chrome_mac_bash": f"curl '{URL}' \\\n  -H 'accept: application/json' \\\n  -b '{COOKIES}' \\\n  --data-raw '{{}}'",
    "safari": (f"curl '{URL}' \\\n-X 'POST' \\\n-H 'Content-Type: application/json' \\\n"
               f"-H 'Cookie: {COOKIES}' \\\n--data-raw '{{}}'"),
    "firefox": (f"curl '{URL}' -X POST -H 'Accept: application/json' "
                f"-H 'Cookie: {COOKIES}' --data-raw '{{}}'"),
    "lowercase_header_double_quotes": f'curl "{URL}" -H "cookie: {COOKIES}"',
    "long_flags": f"curl '{URL}' --cookie '{COOKIES}'",
}


def test_every_browser_shape():
    for name, curl in SHAPES.items():
        got = parse_cookies_from_curl(curl)
        assert got == WANT, f"{name}: {got}"


def test_no_cookies_is_a_clear_error():
    try:
        parse_cookies_from_curl(f"curl '{URL}' -H 'accept: application/json'")
    except ValueError as e:
        assert "Could not find cookie string" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_missing_login_cookie_is_named():
    try:
        parse_cookies_from_curl(f"curl '{URL}' -H 'Cookie: lang=en_GB; accessToken=x'")
    except ValueError as e:
        assert "auth-ns:session" in str(e)
    else:
        raise AssertionError("expected ValueError")


if __name__ == "__main__":
    tests = [test_every_browser_shape, test_no_cookies_is_a_clear_error,
             test_missing_login_cookie_is_named]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
