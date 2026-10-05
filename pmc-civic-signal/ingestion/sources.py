
"""
PMC Civic Signal ingestion sources.

Sources
-------
PublicSocialSource
    Operator-curated official PMC Facebook / Instagram links.

RssNewsSource
    Google News RSS.

SocialFeedSource
    Public Reddit / Mastodon RSS.

XSource
    Official X API when X_BEARER_TOKEN is configured.

SocialCrawlInstagramSource
    Public Instagram discovery through SocialCrawl.

MockSource
    Demo data.

Important
---------
No direct Instagram/Facebook website scraping is performed by this project.

Instagram discovery is performed through SocialCrawl's public-data API.
The SocialCrawl API key is read from:

    SOCIALCRAWL_API_KEY

Do NOT hard-code the API key in this file.
"""

import html
import json
import logging
import os
import re

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================================
# PMC / PUNE FILTERS
# ============================================================================

DEFAULT_PMC_KEYWORDS = [
    "pmc",
    "pune municipal corporation",
    "pune municipal",
    "pune mahanagarpalika",
    "punemc",
    "pmc care",
    "@pmcpune",
    "pmc pune",
    "पुणे महानगरपालिका",
    "पुणे मनपा",
    "पुणे महानगर पालिका",
    "पुणे महापालिका",
    "पुणे मनपा",
]

# These are useful for Instagram discovery where a post may discuss
# a PMC-related issue without literally writing "PMC".
DEFAULT_PUNE_KEYWORDS = [
    "pune",
    "pune city",
    "punecity",
    "pune city news",
    "पुणे",
    "पुणे शहर",
]

DEFAULT_CIVIC_KEYWORDS = [
    "municipal",
    "corporation",
    "civic",
    "civic body",
    "municipality",
    "mahanagarpalika",
    "महानगरपालिका",
    "महापालिका",
    "मनपा",
    "पालिका",

    "road",
    "roads",
    "pothole",
    "potholes",
    "traffic",
    "water supply",
    "water",
    "garbage",
    "waste",
    "drainage",
    "sewer",
    "sewage",
    "streetlight",
    "street lights",
    "flood",
    "flooding",
    "rain",
    "sanitation",
    "encroachment",
    "construction",
    "footpath",
    "flyover",
    "bridge",
    "fire",
    "fire brigade",
    "property tax",
    "tax",
    "ward",
    "bus",
    "parking",
    "pollution",
    "tree",
    "trees",
    "garden",
    "hospital",
    "school",
    "development",
    "tender",
    "project",
    "complaint",
    "complaints",

    "रस्ता",
    "रस्ते",
    "खड्डा",
    "खड्डे",
    "वाहतूक",
    "पाणी",
    "पाणीपुरवठा",
    "कचरा",
    "नाले",
    "गटार",
    "पथदिवे",
    "पूर",
    "पावसाळा",
    "अतिक्रमण",
    "फूटपाथ",
    "पूल",
    "अग्निशमन",
    "मालमत्ता कर",
    "प्रभाग",
    "उद्यान",
    "रुग्णालय",
    "शाळा",
]


def _env_list(
    name: str,
    default: Iterable[str],
) -> List[str]:
    """
    Read comma-separated environment variable.

    Example:
        CIVIC_PMC_KEYWORDS=pmc,punemc,pune municipal corporation
    """

    raw = os.environ.get(name)

    if not raw:
        return list(default)

    return [
        item.strip()
        for item in raw.split(",")
        if item.strip()
    ]


def _contains_term(
    text: str,
    keyword: str,
) -> bool:
    """
    Unicode-safe-ish keyword matching.

    Exact boundaries are used for Latin and Devanagari text where possible.
    """
    keyword = str(keyword or "").strip()

    if not keyword:
        return False

    text = str(text or "")

    pattern = (
        r"(?<![a-z0-9\u0900-\u097F])"
        + re.escape(keyword.casefold())
        + r"(?![a-z0-9\u0900-\u097F])"
    )

    return bool(
        re.search(
            pattern,
            text.casefold(),
        )
    )


def mentions_pmc(
    text: str,
) -> bool:
    """
    Check whether text explicitly mentions PMC / Pune Municipal Corporation.
    """

    keywords = _env_list(
        "CIVIC_PMC_KEYWORDS",
        DEFAULT_PMC_KEYWORDS,
    )

    for keyword in keywords:
        if _contains_term(text, keyword):
            return True

    return False


def mentions_pune(
    text: str,
) -> bool:
    """
    Check whether text appears to mention Pune.
    """

    keywords = _env_list(
        "CIVIC_PUNE_KEYWORDS",
        DEFAULT_PUNE_KEYWORDS,
    )

    for keyword in keywords:
        if _contains_term(text, keyword):
            return True

    return False


def mentions_civic_topic(
    text: str,
) -> bool:
    """
    Check whether text contains a civic/municipal topic.
    """

    keywords = _env_list(
        "CIVIC_TOPIC_KEYWORDS",
        DEFAULT_CIVIC_KEYWORDS,
    )

    for keyword in keywords:
        if _contains_term(text, keyword):
            return True

    return False


def mentions_pune_civic(
    text: str,
) -> bool:
    """
    Instagram-specific relevance check.

    A post is considered relevant when:

        PMC keyword
            OR
        Pune keyword + civic topic

    This is deliberately broader than mentions_pmc().

    Example accepted:
        "PMC announces new water supply schedule"

    Example accepted:
        "Pune roads badly damaged after rain"

    Example rejected:
        "Best cafe in Pune"
    """

    if mentions_pmc(text):
        return True

    return (
        mentions_pune(text)
        and mentions_civic_topic(text)
    )


def pmc_only(
    posts: List[dict],
) -> List[dict]:
    """
    Existing PMC-only filter used by news/social sources.
    """

    if (
        os.environ.get(
            "CIVIC_PMC_ONLY",
            "1",
        )
        == "0"
    ):
        return posts

    return [
        post
        for post in posts
        if mentions_pmc(
            post.get(
                "text",
                "",
            )
        )
    ]


# ============================================================================
# HTTP
# ============================================================================

FEED_USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; PMC-Civic-Signal/1.0; civic issue dashboard)"
)


def fetch_feed(
    url: str,
):
    """
    Download RSS/XML feed using requests and parse with feedparser.
    """

    import feedparser
    import requests

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent": FEED_USER_AGENT,
                "Accept": (
                    "application/rss+xml, "
                    "application/xml, "
                    "text/xml, */*"
                ),
            },
            timeout=20,
        )

    except Exception as error:

        logger.warning(
            "Feed download FAILED (%s: %s): %s",
            type(error).__name__,
            error,
            url,
        )

        return []

    if response.status_code != 200:

        logger.warning(
            "Feed returned HTTP %s: %s",
            response.status_code,
            url,
        )

        return []

    try:

        parsed = feedparser.parse(
            response.content
        )

    except Exception:

        logger.exception(
            "Feed parsing failed: %s",
            url,
        )

        return []

    if not parsed.entries:

        logger.warning(
            "Feed downloaded OK but has 0 entries "
            "(bozo=%s): %s",
            getattr(
                parsed,
                "bozo",
                None,
            ),
            url,
        )

    return parsed.entries


# ============================================================================
# SOCIALCRAWL HELPERS
# ============================================================================

SOCIALCRAWL_BASE_URL = (
    "https://www.socialcrawl.dev/v1"
)


def _socialcrawl_key() -> str:
    return (
        os.environ.get(
            "SOCIALCRAWL_API_KEY",
            "",
        )
        .strip()
    )


def _socialcrawl_request(
    path: str,
    params: Optional[dict] = None,
    timeout: int = 45,
) -> Optional[dict]:
    """
    Make a SocialCrawl API request.

    Credentials are supplied through the x-api-key header and are never
    included in log messages.
    """

    import requests

    api_key = _socialcrawl_key()

    if not api_key:
        return None

    url = (
        SOCIALCRAWL_BASE_URL.rstrip("/")
        + "/"
        + path.lstrip("/")
    )

    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
        "User-Agent": FEED_USER_AGENT,
    }

    try:

        response = requests.get(
            url,
            params=params or {},
            headers=headers,
            timeout=timeout,
        )

    except Exception as error:

        logger.warning(
            "SocialCrawl request failed (%s: %s): %s",
            type(error).__name__,
            error,
            path,
        )

        return None

    if response.status_code != 200:

        # Never log the full URL because it may contain request parameters.
        try:
            payload = response.json()
        except Exception:
            payload = {}

        logger.warning(
            "SocialCrawl returned HTTP %s for %s: %s",
            response.status_code,
            path,
            payload.get("error")
            or payload.get("message")
            or "unknown error",
        )

        return None

    try:

        payload = response.json()

    except Exception:

        logger.warning(
            "SocialCrawl returned non-JSON data for %s",
            path,
        )

        return None

    if payload.get("success") is False:

        logger.warning(
            "SocialCrawl API error for %s: %s",
            path,
            payload.get("error")
            or payload.get("message"),
        )

        return None

    return payload


def _clean_html(
    value: Any,
) -> str:
    """
    Convert caption/HTML-ish values into plain text.
    """

    if value is None:
        return ""

    text = str(value)

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = html.unescape(
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _first_value(
    obj: Any,
    paths: Iterable[str],
) -> Any:
    """
    Return the first non-empty nested value.

    Supports paths such as:
        post.url
        post.content.text
        author.username
    """

    if not isinstance(
        obj,
        dict,
    ):
        return None

    for path in paths:

        current = obj

        try:

            for part in path.split("."):

                if not isinstance(
                    current,
                    dict,
                ):
                    current = None
                    break

                current = current.get(
                    part
                )

            if current not in (
                None,
                "",
                [],
            ):
                return current

        except Exception:
            continue

    return None


def _iter_socialcrawl_posts(
    payload: dict,
) -> Iterable[dict]:
    """
    Extract post dictionaries from SocialCrawl's unified envelope.

    SocialCrawl normally returns:
        data.items[].post

    but this function also tolerates:
        data.items[]
        data.posts[]
        data.results[]
        data[]
    """

    data = payload.get(
        "data",
        {},
    )

    if isinstance(
        data,
        dict,
    ):

        items = data.get(
            "items"
        )

        if isinstance(
            items,
            list,
        ):

            for item in items:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                post = item.get(
                    "post"
                )

                if isinstance(
                    post,
                    dict,
                ):
                    yield post
                else:
                    yield item

            return

        for key in (
            "posts",
            "results",
            "data",
        ):

            rows = data.get(
                key
            )

            if isinstance(
                rows,
                list,
            ):

                for row in rows:

                    if isinstance(
                        row,
                        dict,
                    ):

                        post = row.get(
                            "post"
                        )

                        if isinstance(
                            post,
                            dict,
                        ):
                            yield post
                        else:
                            yield row

                return

        # Some endpoints can return a single post.
        if any(
            key in data
            for key in (
                "url",
                "shortcode",
                "content",
                "caption",
                "published_at",
            )
        ):
            yield data

        return

    if isinstance(
        data,
        list,
    ):

        for row in data:

            if isinstance(
                row,
                dict,
            ):
                yield row


def _socialcrawl_post_to_report(
    post: dict,
    discovery: str,
) -> Optional[dict]:
    """
    Convert SocialCrawl post schema into this project's normalized schema.
    """

    content = _first_value(
        post,
        [
            "content.text",
            "content",
            "caption",
            "text",
        ],
    )

    text = _clean_html(
        content
    )

    if not text:
        return None

    url = _first_value(
        post,
        [
            "url",
            "permalink",
            "link",
        ],
    )

    media_url = _first_value(
        post,
        [
            "media_url",
            "image_url",
            "thumbnail_url",
            "media.url",
            "image.url",
        ],
    )

    posted_at = _first_value(
        post,
        [
            "published_at",
            "created_at",
            "timestamp",
            "date",
        ],
    )

    author_username = _first_value(
        post,
        [
            "author.username",
            "user.username",
            "owner.username",
        ],
    )

    author_name = _first_value(
        post,
        [
            "author.display_name",
            "author.name",
            "user.name",
            "owner.name",
        ],
    )

    source_name = (
        "@"
        + str(author_username)
        if author_username
        else "Instagram"
    )

    if author_name:
        source_name += (
            " · "
            + str(author_name)
        )

    return {
        "source": "instagram",
        "lat": None,
        "lon": None,
        "text": text,
        "posted_at": posted_at,
        "place_hint": None,
        "url": str(url).strip()
        if url
        else None,
        "media_url": str(media_url).strip()
        if media_url
        else None,
        "source_name": source_name,
        "embed": True,
        "instagram_author": (
            str(author_username)
            if author_username
            else None
        ),
        "instagram_discovery": discovery,
    }


# ============================================================================
# SOCIALCRAWL INSTAGRAM
# ============================================================================

class SocialCrawlInstagramSource:
    """
    Discover public Instagram civic-news content using SocialCrawl.

    The source deliberately does NOT depend on one Instagram account.

    Discovery modes
    ---------------
    1. Hashtag feeds
       /v1/instagram/search/hashtag

    2. Instagram reels search
       /v1/instagram/search/reels

    Optional:
        3. Explicit profile monitoring
           /v1/instagram/profile/posts

    Environment
    -----------
    SOCIALCRAWL_API_KEY
        Required.

    CIVIC_INSTAGRAM_ENABLED
        Default: 1.

    CIVIC_INSTAGRAM_HASHTAGS
        Comma-separated hashtags.

    CIVIC_INSTAGRAM_REEL_QUERIES
        Comma-separated search phrases.

    CIVIC_INSTAGRAM_PROFILES
        Optional comma-separated Instagram usernames.

    CIVIC_INSTAGRAM_HASHTAG_PAGES
        Number of recent hashtag pages per hashtag.
        Default: 1.

    CIVIC_INSTAGRAM_REEL_PAGES
        Number of reel-search pages per query.
        Default: 1.

    CIVIC_INSTAGRAM_PROFILE_PAGES
        Number of profile-post pages per profile.
        Default: 1.

    CIVIC_INSTAGRAM_MAX_RESULTS
        Maximum normalized Instagram posts retained.
        Default: 100.

    CIVIC_INSTAGRAM_MIN_RELEVANCE
        If 1, retain only PMC mentions OR Pune+civic posts.
        Default: 1.

    Default hashtags
    ----------------
    pmcpune
    punemc
    punenews
    punecivic
    punemunicipalcorporation
    punemahanagarpalika
    पुणेमहानगरपालिका
    पुणेमनपा

    Default reel searches
    ---------------------
    PMC Pune
    Pune Municipal Corporation
    Pune civic
    Pune municipal
    Pune roads
    Pune traffic
    Pune water
    Pune garbage
    Pune drainage
    """

    DEFAULT_HASHTAGS = [
        "pmcpune",
        "punemc",
        "punenews",
        "punecivic",
        "punemunicipalcorporation",
        "punemahanagarpalika",
        "पुणेमहानगरपालिका",
        "पुणेमनपा",
    ]

    DEFAULT_REEL_QUERIES = [
        "PMC Pune",
        "Pune Municipal Corporation",
        "Pune civic",
        "Pune municipal",
        "Pune roads",
        "Pune traffic",
        "Pune water",
        "Pune garbage",
        "Pune drainage",
    ]

    def available(
        self,
    ) -> bool:

        return (
            os.environ.get(
                "CIVIC_INSTAGRAM_ENABLED",
                "1",
            )
            != "0"
            and bool(
                _socialcrawl_key()
            )
        )

    @staticmethod
    def _positive_int(
        env_name: str,
        default: int,
        minimum: int = 1,
        maximum: int = 20,
    ) -> int:

        try:
            value = int(
                os.environ.get(
                    env_name,
                    str(default),
                )
            )
        except Exception:
            value = default

        return min(
            maximum,
            max(
                minimum,
                value,
            ),
        )

    def hashtags(
        self,
    ) -> List[str]:

        return _env_list(
            "CIVIC_INSTAGRAM_HASHTAGS",
            self.DEFAULT_HASHTAGS,
        )

    def reel_queries(
        self,
    ) -> List[str]:

        return _env_list(
            "CIVIC_INSTAGRAM_REEL_QUERIES",
            self.DEFAULT_REEL_QUERIES,
        )

    def profiles(
        self,
    ) -> List[str]:

        return _env_list(
            "CIVIC_INSTAGRAM_PROFILES",
            [],
        )

    def max_results(
        self,
    ) -> int:

        return self._positive_int(
            "CIVIC_INSTAGRAM_MAX_RESULTS",
            100,
            1,
            500,
        )

    def _is_relevant(
        self,
        post: dict,
    ) -> bool:

        if (
            os.environ.get(
                "CIVIC_INSTAGRAM_MIN_RELEVANCE",
                "1",
            )
            == "0"
        ):
            return True

        text = post.get(
            "text",
            "",
        )

        return mentions_pune_civic(
            text
        )

    def _add_if_relevant(
        self,
        output: List[dict],
        post: dict,
    ) -> None:

        if not self._is_relevant(
            post
        ):
            return

        output.append(
            post
        )

    def _fetch_hashtag(
        self,
        hashtag: str,
        output: List[dict],
    ) -> None:

        max_pages = self._positive_int(
            "CIVIC_INSTAGRAM_HASHTAG_PAGES",
            1,
            1,
            10,
        )

        cursor = None

        for page_no in range(
            max_pages
        ):

            params = {
                "hashtag": hashtag,
                "type": "recent",
            }

            if cursor:
                params["cursor"] = cursor

            payload = _socialcrawl_request(
                "instagram/search/hashtag",
                params=params,
                timeout=45,
            )

            if not payload:
                return

            found = 0

            for post in _iter_socialcrawl_posts(
                payload
            ):

                normalized = (
                    _socialcrawl_post_to_report(
                        post,
                        "hashtag:"
                        + hashtag,
                    )
                )

                if not normalized:
                    continue

                before = len(
                    output
                )

                self._add_if_relevant(
                    output,
                    normalized,
                )

                if len(output) > before:
                    found += 1

            pagination = payload.get(
                "pagination",
                {},
            )

            if not isinstance(
                pagination,
                dict,
            ):
                pagination = {}

            if not pagination.get(
                "has_more"
            ):
                break

            next_cursor = (
                pagination.get(
                    "next_cursor"
                )
                or pagination.get(
                    "cursor"
                )
            )

            if not next_cursor:
                break

            cursor = next_cursor

            logger.info(
                "SocialCrawl Instagram hashtag "
                "%s page %d: %d relevant post(s)",
                hashtag,
                page_no + 1,
                found,
            )

    def _fetch_reels(
        self,
        query: str,
        output: List[dict],
    ) -> None:

        max_pages = self._positive_int(
            "CIVIC_INSTAGRAM_REEL_PAGES",
            1,
            1,
            10,
        )

        for page_no in range(
            1,
            max_pages + 1,
        ):

            payload = _socialcrawl_request(
                "instagram/search/reels",
                params={
                    "query": query,
                    "page": page_no,
                },
                timeout=45,
            )

            if not payload:
                return

            found = 0

            for post in _iter_socialcrawl_posts(
                payload
            ):

                normalized = (
                    _socialcrawl_post_to_report(
                        post,
                        "reels:"
                        + query,
                    )
                )

                if not normalized:
                    continue

                before = len(
                    output
                )

                self._add_if_relevant(
                    output,
                    normalized,
                )

                if len(output) > before:
                    found += 1

            logger.info(
                "SocialCrawl Instagram reels "
                "%r page %d: %d relevant post(s)",
                query,
                page_no,
                found,
            )

            # Search/reels pagination uses page rather than the hashtag
            # cursor. Stop if this response explicitly says there are no more.
            data = payload.get(
                "data",
                {},
            )

            if isinstance(
                data,
                dict,
            ):

                pagination = data.get(
                    "pagination",
                    {},
                )

                if isinstance(
                    pagination,
                    dict,
                ) and pagination.get(
                    "has_more"
                ) is False:
                    break

    def _fetch_profile(
        self,
        handle: str,
        output: List[dict],
    ) -> None:

        max_pages = self._positive_int(
            "CIVIC_INSTAGRAM_PROFILE_PAGES",
            1,
            1,
            10,
        )

        cursor = None

        for page_no in range(
            max_pages
        ):

            params = {
                "handle": handle,
            }

            if cursor:
                params["next_max_id"] = cursor

            payload = _socialcrawl_request(
                "instagram/profile/posts",
                params=params,
                timeout=45,
            )

            if not payload:
                return

            found = 0

            for post in _iter_socialcrawl_posts(
                payload
            ):

                normalized = (
                    _socialcrawl_post_to_report(
                        post,
                        "profile:@"
                        + handle.lstrip("@"),
                    )
                )

                if not normalized:
                    continue

                # For explicitly configured profiles, still require PMC/Pune
                # relevance unless the operator disables the filter.
                before = len(
                    output
                )

                self._add_if_relevant(
                    output,
                    normalized,
                )

                if len(output) > before:
                    found += 1

            logger.info(
                "SocialCrawl Instagram profile "
                "@%s page %d: %d relevant post(s)",
                handle.lstrip("@"),
                page_no + 1,
                found,
            )

            data = payload.get(
                "data",
                {},
            )

            pagination = (
                data.get(
                    "pagination",
                    {},
                )
                if isinstance(
                    data,
                    dict,
                )
                else {}
            )

            if not isinstance(
                pagination,
                dict,
            ):
                pagination = {}

            next_cursor = (
                pagination.get(
                    "next_cursor"
                )
                or pagination.get(
                    "next_max_id"
                )
            )

            if not next_cursor:
                break

            cursor = next_cursor

    @staticmethod
    def _dedupe(
        posts: List[dict],
    ) -> List[dict]:

        result = []
        seen = set()

        for post in posts:

            url = (
                post.get("url")
                or ""
            ).strip().lower()

            text = re.sub(
                r"\W+",
                " ",
                str(
                    post.get(
                        "text",
                        "",
                    )
                ).casefold(),
            ).strip()

            key = (
                url
                if url
                else text
            )

            if not key:
                continue

            if key in seen:
                continue

            seen.add(key)
            result.append(
                post
            )

        return result

    def fetch(
        self,
    ) -> List[dict]:

        if not self.available():

            if (
                os.environ.get(
                    "CIVIC_INSTAGRAM_ENABLED",
                    "1",
                )
                != "0"
            ):
                logger.info(
                    "SocialCrawlInstagramSource unavailable: "
                    "SOCIALCRAWL_API_KEY not configured"
                )

            return []

        output = []

        # ---------------------------------------------------------------
        # Hashtag discovery
        # ---------------------------------------------------------------

        for hashtag in self.hashtags():

            self._fetch_hashtag(
                hashtag,
                output,
            )

            if len(output) >= self.max_results():
                break

        # ---------------------------------------------------------------
        # Reels discovery
        # ---------------------------------------------------------------

        if len(output) < self.max_results():

            for query in self.reel_queries():

                self._fetch_reels(
                    query,
                    output,
                )

                if len(output) >= self.max_results():
                    break

        # ---------------------------------------------------------------
        # Optional explicit profile discovery
        # ---------------------------------------------------------------

        if len(output) < self.max_results():

            for handle in self.profiles():

                self._fetch_profile(
                    handle,
                    output,
                )

                if len(output) >= self.max_results():
                    break

        output = self._dedupe(
            output
        )

        output = output[
            : self.max_results()
        ]

        logger.info(
            "SocialCrawlInstagramSource: "
            "%d relevant public Instagram post(s)",
            len(output),
        )

        return output


# ============================================================================
# GRAPH API HELPERS
# ============================================================================

def _log_graph_error(
    name: str,
    error: Exception,
) -> None:

    detail = ""

    response = getattr(
        error,
        "response",
        None,
    )

    if response is not None:

        try:

            payload = response.json()

            err = payload.get(
                "error",
                {},
            )

            detail = (
                f"HTTP {response.status_code}, "
                f"code {err.get('code')}: "
                f"{err.get('message')}"
            )

        except Exception:

            detail = (
                f"HTTP {response.status_code}"
            )

    logger.warning(
        "%s failed (%s) %s",
        name,
        type(error).__name__,
        detail,
    )


def _graph_pages(
    url: str,
    params: dict,
    label: str,
):
    """
    Yield Graph API pages without exposing credential-bearing URLs.
    """

    import requests

    max_pages = max(
        1,
        int(
            os.environ.get(
                "CIVIC_META_MAX_PAGES",
                "5",
            )
        ),
    )

    page_no = 0

    next_url = url
    next_params = params

    while (
        next_url
        and page_no < max_pages
    ):

        try:

            response = requests.get(
                next_url,
                params=next_params,
                timeout=20,
            )

            response.raise_for_status()

            payload = response.json()

        except Exception as error:

            _log_graph_error(
                f"{label} page {page_no + 1}",
                error,
            )

            break

        yield payload.get(
            "data",
            [],
        )

        page_no += 1

        next_url = (
            payload.get(
                "paging"
            )
            or {}
        ).get(
            "next"
        )

        next_params = None


# ============================================================================
# MOCK
# ============================================================================

class MockSource:
    """
    Demo source.
    """

    def fetch(
        self,
    ) -> List[dict]:

        path = (
            BASE_DIR
            / "data"
            / "sample_reports.json"
        )

        if not path.exists():

            logger.warning(
                "Mock data file not found: %s",
                path,
            )

            return []

        try:

            raw = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

            return raw.get(
                "reports",
                [],
            )

        except Exception:

            logger.exception(
                "Could not load mock reports"
            )

            return []


# ============================================================================
# X
# ============================================================================

class XSource:

    def available(
        self,
    ) -> bool:

        return bool(
            os.environ.get(
                "X_BEARER_TOKEN"
            )
        )

    DEFAULT_QUERY = (
        '(PMC OR "Pune Municipal Corporation" OR @PMCPune) '
        '(pothole OR traffic OR "water supply" OR garbage OR '
        'streetlight OR drainage OR "power cut")'
    )

    def fetch(
        self,
        query: str = None,
    ) -> List[dict]:

        if not self.available():
            return []

        query = (
            query
            or os.environ.get(
                "CIVIC_X_QUERY"
            )
            or self.DEFAULT_QUERY
        )

        max_results = min(
            100,
            max(
                10,
                int(
                    os.environ.get(
                        "CIVIC_X_MAX_RESULTS",
                        "25",
                    )
                ),
            ),
        )

        try:

            import requests

            headers = {
                "Authorization": (
                    "Bearer "
                    + os.environ[
                        "X_BEARER_TOKEN"
                    ]
                )
            }

            params = {
                "query": (
                    f"{query} "
                    "lang:en "
                    "-is:retweet"
                ),
                "max_results": max_results,
                "tweet.fields": (
                    "created_at,geo"
                ),
            }

            response = requests.get(
                "https://api.x.com/2/tweets/search/recent",
                headers=headers,
                params=params,
                timeout=15,
            )

            response.raise_for_status()

            output = []

            for tweet in (
                response.json().get(
                    "data",
                    [],
                )
            ):

                output.append(
                    {
                        "source": "x",
                        "lat": None,
                        "lon": None,
                        "text": tweet.get(
                            "text",
                            "",
                        ),
                        "posted_at": tweet.get(
                            "created_at"
                        ),
                        "place_hint": None,
                        "url": (
                            "https://x.com/i/web/status/"
                            + str(
                                tweet.get(
                                    "id"
                                )
                            )
                            if tweet.get("id")
                            else None
                        ),
                        "source_name": "X",
                    }
                )

            return pmc_only(
                output
            )

        except Exception:

            logger.exception(
                "X ingestion failed"
            )

            return []


# ============================================================================
# OFFICIAL PMC SOCIAL
# ============================================================================

class PublicSocialSource:
    """
    Token-free source for operator-curated official PMC
    Facebook / Instagram posts.

    File:
        data/public_social_posts.json

    Override:
        CIVIC_PUBLIC_SOCIAL_FILE
    """

    DEFAULT_FILE = (
        BASE_DIR
        / "data"
        / "public_social_posts.json"
    )

    def __init__(
        self,
        path=None,
    ):

        self.path = Path(
            path
            or os.environ.get(
                "CIVIC_PUBLIC_SOCIAL_FILE",
                self.DEFAULT_FILE,
            )
        )

    def available(
        self,
    ) -> bool:

        return self.path.exists()

    @staticmethod
    def _source_from_url(
        url: str,
    ) -> str | None:

        value = (
            url or ""
        ).lower()

        if (
            "facebook.com"
            in value
            or "fb.watch"
            in value
        ):
            return "facebook"

        if (
            "instagram.com"
            in value
        ):
            return "instagram"

        return None

    def fetch(
        self,
    ) -> List[dict]:

        if not self.available():
            return []

        try:

            raw = json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )

            posts = (
                raw
                if isinstance(
                    raw,
                    list,
                )
                else raw.get(
                    "posts",
                    [],
                )
            )

        except Exception:

            logger.exception(
                "Public social post config "
                "could not be read: %s",
                self.path,
            )

            return []

        output = []

        for item in posts:

            if not isinstance(
                item,
                dict,
            ):
                continue

            url = str(
                item.get("url")
                or ""
            ).strip()

            source = str(
                item.get("source")
                or self._source_from_url(
                    url
                )
                or ""
            ).lower().strip()

            if (
                source
                not in {
                    "facebook",
                    "instagram",
                }
                or not url
            ):
                continue

            text = str(
                item.get("text")
                or item.get("caption")
                or item.get("title")
                or ""
            ).strip()

            if not text:

                text = (
                    "PMC official "
                    "social-media update"
                )

            output.append(
                {
                    "source": source,
                    "lat": None,
                    "lon": None,
                    "text": text,
                    "posted_at": (
                        item.get("posted_at")
                        or item.get("date")
                    ),
                    "place_hint": (
                        item.get("ward")
                        or item.get("location")
                        or item.get("place_hint")
                    ),
                    "url": url,
                    "media_url": (
                        item.get("media_url")
                        or item.get("image")
                    ),
                    "source_name": (
                        "PMC Pune · "
                        + source.title()
                    ),
                    "embed": True,
                }
            )

        logger.info(
            "Public PMC social links: "
            "%d post(s) loaded from %s",
            len(output),
            self.path,
        )

        return output


# Explicit source names used by the portal/status layer. These wrappers remain
# token-free: they read operator-supplied public post URLs and captions only.
class MetaMentionsSource(PublicSocialSource):
    pass

class InstagramMentionsSource(PublicSocialSource):
    pass

MetaOfficialSource = MetaMentionsSource


# ============================================================================
# RSS NEWS
# ============================================================================

class RssNewsSource:
    """
    Google News RSS source.
    """

    DEFAULT_FEEDS = [

        (
            "https://news.google.com/rss/search?"
            "q=%22Pune%20Municipal%20Corporation%22"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),

        (
            "https://news.google.com/rss/search?"
            "q=PMC%20Pune%20civic"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),

        (
            "https://news.google.com/rss/search?"
            "q=PMC%20Pune%20road%20traffic%20garbage%20water"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),
    ]

    def feeds(
        self,
    ) -> List[str]:

        raw = os.environ.get(
            "CIVIC_NEWS_RSS_FEEDS"
        )

        if raw:

            return [
                url.strip()
                for url in raw.split(",")
                if url.strip()
            ]

        return self.DEFAULT_FEEDS

    def available(
        self,
    ) -> bool:

        return bool(
            self.feeds()
        )

    def fetch(
        self,
    ) -> List[dict]:

        try:

            output = []

            for url in self.feeds():

                entries = fetch_feed(
                    url
                )

                for entry in entries[:25]:

                    title = str(
                        entry.get(
                            "title",
                            "",
                        )
                    )

                    summary = (
                        entry.get(
                            "summary",
                            "",
                        )
                        or ""
                    )

                    summary = html.unescape(
                        re.sub(
                            r"<[^>]+>",
                            " ",
                            summary,
                        )
                    )

                    text = (
                        title
                        + ". "
                        + summary[:500]
                    ).strip(
                        ". "
                    )

                    if not text:
                        continue

                    source_info = entry.get(
                        "source"
                    )

                    if isinstance(
                        source_info,
                        dict,
                    ):

                        source_name = (
                            source_info.get(
                                "title"
                            )
                        )

                    else:

                        source_name = None

                    output.append(
                        {
                            "source": "news",
                            "lat": None,
                            "lon": None,
                            "text": text,
                            "posted_at": (
                                entry.get(
                                    "published"
                                )
                                or entry.get(
                                    "updated"
                                )
                            ),
                            "place_hint": None,
                            "url": entry.get(
                                "link"
                            ),
                            "source_name": (
                                source_name
                            ),
                        }
                    )

            kept = pmc_only(
                output
            )

            logger.info(
                "RssNewsSource: "
                "%d item(s) fetched, "
                "%d mention PMC",
                len(output),
                len(kept),
            )

            return kept

        except Exception:

            logger.exception(
                "RSS ingestion failed"
            )

            return []


# ============================================================================
# SOCIAL RSS
# ============================================================================

class SocialFeedSource:
    """
    Reddit / Mastodon public RSS feeds.

    Disable:
        CIVIC_DISABLE_SOCIAL=1

    Custom feeds:
        CIVIC_SOCIAL_FEEDS=url1,url2
    """

    DEFAULT_FEEDS = [

        (
            "https://www.reddit.com/"
            "r/pune/new.rss?limit=100"
        ),

        (
            "https://www.reddit.com/"
            "r/pune/search.rss?"
            "q=PMC+OR+%22Pune+Municipal+Corporation%22"
            "&restrict_sr=1&sort=new"
        ),

        (
            "https://mastodon.social/"
            "tags/pune.rss"
        ),
    ]

    def available(
        self,
    ) -> bool:

        return (
            os.environ.get(
                "CIVIC_DISABLE_SOCIAL"
            )
            != "1"
        )

    def feeds(
        self,
    ) -> List[str]:

        raw = os.environ.get(
            "CIVIC_SOCIAL_FEEDS"
        )

        if raw:

            return [
                url.strip()
                for url in raw.split(",")
                if url.strip()
            ]

        return self.DEFAULT_FEEDS

    @staticmethod
    def _plain(
        value: str,
    ) -> str:

        text = re.sub(
            r"<[^>]+>",
            " ",
            value or "",
        )

        text = html.unescape(
            text
        )

        return re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    def fetch(
        self,
    ) -> List[dict]:

        if not self.available():
            return []

        output = []

        for url in self.feeds():

            try:

                for entry in fetch_feed(
                    url
                )[:50]:

                    title = self._plain(
                        entry.get(
                            "title",
                            "",
                        )
                    )

                    body = self._plain(
                        entry.get(
                            "summary",
                            "",
                        )
                    )[:400]

                    if (
                        body
                        and body != title
                    ):

                        text = (
                            title
                            + ". "
                            + body
                        ).strip(
                            ". "
                        )

                    else:

                        text = title

                    if not text:
                        continue

                    if "reddit.com" in url:

                        host = "reddit"

                    elif "mastodon" in url:

                        host = "mastodon"

                    else:

                        host = "social"

                    output.append(
                        {
                            "source": host,
                            "lat": None,
                            "lon": None,
                            "text": text,
                            "posted_at": (
                                entry.get(
                                    "published"
                                )
                                or entry.get(
                                    "updated"
                                )
                            ),
                            "place_hint": None,
                            "url": entry.get(
                                "link"
                            ),
                            "source_name": host.title(),
                        }
                    )

            except Exception:

                logger.exception(
                    "Social feed failed: %s",
                    url,
                )

        kept = pmc_only(
            output
        )

        logger.info(
            "SocialFeedSource: "
            "%d post(s) fetched, "
            "%d mention PMC",
            len(output),
            len(kept),
        )

        return kept


# ============================================================================
# ALL SOURCES
# ============================================================================

def all_sources():
    """
    Return configured sources.

    Live sources:
        PublicSocialSource
        RssNewsSource
        SocialCrawlInstagramSource
        SocialFeedSource
        XSource

    Mock data is included only when:
        CIVIC_INCLUDE_MOCK=1

    or when no live source is available.
    """

    live = [
        MetaMentionsSource(),
        RssNewsSource(),
        SocialCrawlInstagramSource(),
    ]

    # Optional public Reddit / Mastodon feeds.
    if (
        os.environ.get(
            "CIVIC_DISABLE_SOCIAL"
        )
        != "1"
    ):
        live.append(
            SocialFeedSource()
        )

    # Optional X source.
    if os.environ.get(
        "X_BEARER_TOKEN"
    ):
        live.append(
            XSource()
        )

    any_live = any(
        source.available()
        for source in live
        if hasattr(
            source,
            "available",
        )
    )

    include_mock = (
        os.environ.get(
            "CIVIC_INCLUDE_MOCK"
        )
        == "1"
        or not any_live
    )

    if include_mock:

        return [
            MockSource()
        ] + live

    return live
