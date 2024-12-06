import httpx
import folioclient


def _request_get(
    url, params, timeout, max_retries, folio_client: folioclient.FolioClient
):
    with folio_client.get_folio_http_client() as http_client:
        r = 0
        while r < max_retries:
            try:
                return http_client.get(
                    url,
                    params=params,
                    headers=folio_client.okapi_headers,
                    timeout=timeout,
                )
            except httpx.TimeoutException:
                pass
            r += 1
        return http_client.get(
            url, params=params, headers=folio_client.okapi_headers, timeout=timeout
        )
