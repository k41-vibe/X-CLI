"""x-client-transaction-id の生成。

XClientTransaction(pip: XClientTransaction, import: x_client_transaction)を利用。
x.com/home と ondemand.s.js から検証データを取り出して ClientTransaction を1回構築し、
以降はパスごとに transaction_id を生成する。生成自体に認証は不要(guest ヘッダで取得)。
"""
from __future__ import annotations

from urllib.parse import urlparse

import bs4
import requests

from x_client_transaction import ClientTransaction
from x_client_transaction.utils import generate_headers, get_ondemand_file_url


class TransactionGenerator:
    def __init__(self, user_agent: str | None = None):
        self._ct: ClientTransaction | None = None
        self._user_agent = user_agent

    def _init(self) -> ClientTransaction:
        session = requests.Session()
        session.headers = generate_headers()
        if self._user_agent:
            session.headers["User-Agent"] = self._user_agent

        home = session.get("https://x.com/home", timeout=20)
        home_soup = bs4.BeautifulSoup(home.content, "html.parser")

        ondemand_url = get_ondemand_file_url(response=home_soup)
        ondemand = session.get(ondemand_url, timeout=20).text

        return ClientTransaction(
            home_page_response=home_soup, ondemand_file_response=ondemand
        )

    def generate(self, method: str, url: str) -> str:
        if self._ct is None:
            self._ct = self._init()
        path = urlparse(url).path
        return self._ct.generate_transaction_id(method=method, path=path)
