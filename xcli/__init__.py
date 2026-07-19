"""Xcli - X (Twitter) の非公式 GraphQL API を叩く自己利用 CLI。

エンドポイント形式:  https://x.com/i/api/graphql/{queryId}/{Operation}
認証:  公開 Bearer + auth_token/ct0 Cookie + x-csrf-token(=ct0) + x-client-transaction-id
"""

__version__ = "0.1.0"
