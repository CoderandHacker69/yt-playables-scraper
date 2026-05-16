import json
import os
import re
import ssl
from typing import Any, Dict, List

try:
    import requests
except ModuleNotFoundError:
    requests = None


class SimpleResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


def http_get(url: str, headers: Dict[str, str], timeout: int = 10) -> SimpleResponse:
    if requests is not None:
        return requests.get(url, headers=headers, timeout=timeout)

    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
        return SimpleResponse(status_code=response.getcode(), text=text)


def http_post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 10) -> SimpleResponse:
    if requests is not None:
        return requests.post(url, headers={**headers, "Content-Type": "application/json"}, json=payload, timeout=timeout)

    import urllib.request

    body = json.dumps(payload).encode("utf-8")
    request_headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=request_headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
        return SimpleResponse(status_code=response.getcode(), text=text)


def sanitize_filename(value: str, extension: str = ".jpg") -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value or "playable")
    cleaned = re.sub(r"[\s_-]+", "-", cleaned).strip("-_ ")
    if not cleaned:
        cleaned = "playable"
    return f"{cleaned[:120]}{extension}"


def get_image_extension(image_url: str) -> str:
    if not image_url:
        return ".jpg"
    match = re.search(r"\.(png|jpe?g|gif|webp)(?:[?\\#]|$)", image_url, re.IGNORECASE)
    return f".{match.group(1).lower()}" if match else ".jpg"


def download_image(image_url: str, output_path: str, headers: Dict[str, str], timeout: int = 20) -> bool:
    if not image_url:
        return False

    try:
        if requests is not None:
            response = requests.get(image_url, headers=headers, timeout=timeout)
            status = response.status_code
            content = response.content
        else:
            import urllib.request

            req = urllib.request.Request(image_url, headers=headers)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
                content = response.read()
                status = response.getcode()

        if status != 200 or not content:
            return False

        with open(output_path, "wb") as f:
            f.write(content)
        return True
    except Exception:
        return False


def extract_yt_initial_data(html_source: str) -> Any:
    patterns = [
        r"var ytInitialData = (\{.*?\});</script>",
        r"window\['ytInitialData'\] = (\{.*?\});",
        r"ytInitialData = (\{.*?\});",
    ]

    for pattern in patterns:
        match = re.search(pattern, html_source, re.DOTALL)
        if match:
            return json.loads(match.group(1))

    raise ValueError("Could not extract ytInitialData from page source.")


def extract_json_object(text: str, start_pos: int) -> str:
    depth = 0
    in_string = False
    escape = False

    for idx in range(start_pos, len(text)):
        char = text[idx]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == "\"":
                in_string = False
            continue

        if char == "\"":
            in_string = True
            continue

        if char == "{":
            depth += 1
            if depth == 1:
                start_pos = idx
            continue

        if char == "}":
            depth -= 1
            if depth == 0:
                return text[start_pos: idx + 1]

    raise ValueError("Could not parse JSON object from text.")


def extract_innertube_config(html_source: str) -> Dict[str, Any]:
    api_key_match = re.search(r'"INNERTUBE_API_KEY"\s*:\s*"([^\"]+)"', html_source)
    context_match = re.search(r'"INNERTUBE_CONTEXT"\s*:\s*\{', html_source)

    if not api_key_match or not context_match:
        raise ValueError("Could not extract INNERTUBE config from page source.")

    api_key = api_key_match.group(1)
    context_text = extract_json_object(html_source, context_match.start())
    context = json.loads(context_text)

    return {"api_key": api_key, "context": context}


def extract_playable_items(data: Any) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []

    if isinstance(data, dict):
        if "miniGameCardViewModel" in data:
            model = data["miniGameCardViewModel"]
            title = model.get("title") or "Untitled playable"
            image_url = None
            image_sources = model.get("image", {}).get("sources", [])
            if image_sources and isinstance(image_sources, list):
                image_url = image_sources[0].get("url")

            url = (
                model.get("onTap", {})
                .get("innertubeCommand", {})
                .get("commandMetadata", {})
                .get("webCommandMetadata", {})
                .get("url")
            )

            if not url:
                url = (
                    data.get("onTap", {})
                    .get("innertubeCommand", {})
                    .get("commandMetadata", {})
                    .get("webCommandMetadata", {})
                    .get("url")
                )

            if url:
                if url.startswith("/"):
                    url = f"https://youtube.com{url}"
                results.append(
                    {
                        "title": title,
                        "thumbnail_url": image_url or "No image URL extracted",
                        "regular_url": url,
                    }
                )

        for value in data.values():
            results.extend(extract_playable_items(value))

    elif isinstance(data, list):
        for item in data:
            results.extend(extract_playable_items(item))

    return results


def extract_continuation_tokens(data: Any) -> List[str]:
    tokens: List[str] = []

    if isinstance(data, dict):
        if "continuationCommand" in data and isinstance(data["continuationCommand"], dict):
            token = data["continuationCommand"].get("token")
            if isinstance(token, str):
                tokens.append(token)

        if "continuationEndpoint" in data and isinstance(data["continuationEndpoint"], dict):
            token = data["continuationEndpoint"].get("token")
            if isinstance(token, str):
                tokens.append(token)

        if "continuationItemRenderer" in data and isinstance(data["continuationItemRenderer"], dict):
            tokens.extend(extract_continuation_tokens(data["continuationItemRenderer"]))

        for value in data.values():
            tokens.extend(extract_continuation_tokens(value))

    elif isinstance(data, list):
        for item in data:
            tokens.extend(extract_continuation_tokens(item))

    return tokens


def get_browse_continuation_page(token: str, api_key: str, context: Dict[str, Any], headers: Dict[str, str]) -> Any:
    endpoint = f"https://www.youtube.com/youtubei/v1/browse?key={api_key}"
    payload = {"context": context, "continuation": token}
    response = http_post_json(endpoint, headers=headers, payload=payload, timeout=20)

    if response.status_code != 200:
        raise RuntimeError(f"Continuation request failed: {response.status_code}")

    return json.loads(response.text)


def deep_inspect_playables() -> None:
    catalog_url = "https://www.youtube.com/playables/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    if requests is None:
        print("Warning: 'requests' is not installed. Falling back to urllib for HTTP requests.")

    print("Step 1: Inspecting the main catalog page layout...")
    try:
        response = http_get(catalog_url, headers=headers, timeout=20)
    except Exception as err:
        print(f"Failed to load catalog page: {err}")
        return

    if response.status_code != 200:
        print(f"Failed to load catalog page. Status: {response.status_code}")
        return

    html_source = response.text
    try:
        catalog_json = extract_yt_initial_data(html_source)
    except ValueError:
        print(
            "Error: Could not grab layout configurations. YouTube might be blocking automated inspect requests."
        )
        return

    try:
        innertube_config = extract_innertube_config(html_source)
    except ValueError:
        innertube_config = {}
        print("Warning: Could not extract INNERTUBE config, continued pagination may not work.")

    playable_items = extract_playable_items(catalog_json)
    unique_urls: Dict[str, Dict[str, str]] = {}
    for item in playable_items:
        unique_urls[item["regular_url"]] = item

    images_dir = "images"
    os.makedirs(images_dir, exist_ok=True)
    filename_counts: Dict[str, int] = {}

    continuation_tokens = []
    if innertube_config:
        continuation_tokens = extract_continuation_tokens(catalog_json)
    seen_tokens = set()

    while continuation_tokens and innertube_config:
        token = continuation_tokens.pop(0)
        if token in seen_tokens:
            continue

        print("Loading continuation token:", token[:32] + "...")
        seen_tokens.add(token)

        try:
            page_json = get_browse_continuation_page(token, innertube_config["api_key"], innertube_config["context"], headers)
        except Exception as err:
            print(f"Failed continuation request: {err}")
            continue

        more_items = extract_playable_items(page_json)
        for item in more_items:
            unique_urls[item["regular_url"]] = item

        continuation_tokens.extend(extract_continuation_tokens(page_json))

    if not unique_urls:
        print("No playable entries found in the YouTube playables catalog.")
        return

    print(f"-> Success. Found {len(unique_urls)} distinct playable endpoints to inspect.")

    scraped_manifest: List[Dict[str, str]] = []

    for idx, game_entry in enumerate(unique_urls.values(), 1):
        game_url = game_entry["regular_url"]
        print(f"[{idx}/{len(unique_urls)}] Deep inspecting item: {game_url}")

        try:
            game_page_res = http_get(game_url, headers=headers, timeout=15)
            if game_page_res.status_code != 200:
                print(f"   [!] Skipped: page returned {game_page_res.status_code}")
                continue

            page_code = game_page_res.text
            cdn_match = re.search(r"https://(\d+)\.playables\.usercontent\.goog", page_code)

            title = game_entry["title"]
            image_url = game_entry.get("thumbnail_url")
            image_ext = get_image_extension(image_url)
            image_base = sanitize_filename(title, image_ext)
            count = filename_counts.get(image_base, 0)
            if count:
                stem, ext = os.path.splitext(image_base)
                image_filename = f"{stem}-{count}{ext}"
            else:
                image_filename = image_base
            filename_counts[image_base] = count + 1
            local_image_path = os.path.join(images_dir, image_filename)
            local_image_rel = f"{images_dir}/{image_filename}"

            downloaded = False
            if image_url and image_url != "No image URL extracted":
                downloaded = download_image(image_url, local_image_path, headers, timeout=20)
            if not downloaded:
                local_image_rel = None

            if cdn_match:
                cdn_id = cdn_match.group(1)
                scraped_manifest.append(
                    {
                        "title": title,
                        "thumbnail_url": image_url,
                        "thumbnail_filename": image_filename,
                        "thumbnail_local": local_image_rel,
                        "regular_url": game_url,
                        "cdn_asset_url": f"https://{cdn_id}.playables.usercontent.goog/v/assets/index.html",
                        "asset_id": cdn_id,
                    }
                )
                print(f"   [+] Saved asset map metadata for: '{title}'")
            else:
                print(
                    "   [-] Skipped: Could not locate the playable CDN container URL inside the game page."
                )

        except Exception as err:
            print(f"   [!] Error scanning individual page entry: {err}")
            continue

    output_file = "youtube_playables_scraped.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(scraped_manifest, f, indent=4, ensure_ascii=False)

    print(
        f"\nInspection Complete. Saved {len(scraped_manifest)} items with full details into '{output_file}'!"
    )


if __name__ == "__main__":
    deep_inspect_playables()
