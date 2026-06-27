import re

URL = re.compile(r"https?://[^\s<>\"\)\]]+")
PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def find_unsupported(response: str, chunks: list) -> dict:
    if not response or not chunks:
        return {"urls": set(), "phones": set()}
    chunk_blob = " ".join(c.get("text", "") for c in chunks)
    return {
        "urls": set(URL.findall(response)) - set(URL.findall(chunk_blob)),
        "phones": set(PHONE.findall(response)) - set(PHONE.findall(chunk_blob)),
    }


def scrub(response: str, unsupported: dict) -> str:
    for url in unsupported["urls"]:
        response = response.replace(url, "[unsupported URL removed]")
    for phone in unsupported["phones"]:
        response = response.replace(phone, "[unsupported phone removed]")
    return response
