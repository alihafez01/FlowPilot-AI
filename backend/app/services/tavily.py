import os
import time

import httpx


class TavilyAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class TavilyClient:
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self):
        self.api_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.base_url = os.getenv(
            "TAVILY_BASE_URL",
            "https://api.tavily.com",
        ).rstrip("/")

        timeout = float(
            os.getenv("TAVILY_TIMEOUT_SECONDS", "30")
        )

        if not 1 <= timeout <= 60:
            raise ValueError(
                "TAVILY_TIMEOUT_SECONDS must be between 1 and 60."
            )

        self.timeout = timeout
        self.http = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def __enter__(self):
        if not self.api_key:
            self.http.close()
            raise TavilyAPIError(
                503,
                "TAVILY_API_KEY is not configured in the local environment.",
            )

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.http.close()

    def _post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}/{endpoint}"

        for attempt in range(3):
            try:
                response = self.http.post(url, json=payload)

            except httpx.TimeoutException:
                if attempt < 2:
                    time.sleep(attempt + 1)
                    continue

                raise TavilyAPIError(
                    504,
                    "Tavily request timed out after bounded retries.",
                ) from None

            except httpx.RequestError:
                if attempt < 2:
                    time.sleep(attempt + 1)
                    continue

                raise TavilyAPIError(
                    502,
                    "Could not connect to Tavily after bounded retries.",
                ) from None

            if (
                response.status_code in self.RETRYABLE_STATUS_CODES
                and attempt < 2
            ):
                time.sleep(attempt + 1)
                continue

            if response.status_code >= 400:
                if response.status_code in (401, 403):
                    message = (
                        "Tavily rejected the API key. "
                        "Check your local TAVILY_API_KEY."
                    )
                elif response.status_code == 429:
                    message = (
                        "Tavily rate limit reached. "
                        "Wait before trying again."
                    )
                elif response.status_code in (432, 433):
                    message = (
                        "Tavily account usage limit reached. "
                        "Check your dashboard and free credits."
                    )
                else:
                    message = (
                        f"Tavily request failed with HTTP "
                        f"{response.status_code}."
                    )

                raise TavilyAPIError(response.status_code, message)

            try:
                data = response.json()
            except ValueError:
                raise TavilyAPIError(
                    502,
                    "Tavily returned an invalid JSON response.",
                ) from None

            if not isinstance(data, dict):
                raise TavilyAPIError(
                    502,
                    "Tavily returned an unexpected response format.",
                )

            return data

        raise TavilyAPIError(
            502,
            "Tavily request failed after bounded retries.",
        )

    def search(
        self,
        query: str,
        max_results: int,
        include_domains: list[str],
    ) -> dict:
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "chunks_per_source": 2,
            "topic": "general",
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
            "include_usage": True,
        }

        if include_domains:
            payload["include_domains"] = include_domains

        return self._post("search", payload)

    def extract(self, urls: list[str]) -> dict:
        if not urls:
            return {"results": [], "failed_results": []}

        # This project deliberately caps each extraction batch at 5 URLs.
        if len(urls) > 5:
            raise ValueError(
                "A discovery extraction batch cannot exceed 5 URLs."
            )

        payload = {
            "urls": urls,
            "extract_depth": "basic",
            "format": "markdown",
            "include_usage": True,
        }

        return self._post("extract", payload)